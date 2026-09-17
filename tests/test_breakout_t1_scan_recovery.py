from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import BreakoutEvent, DiscoverySnapshot, ProviderStatus
from app.services.breakouts.realtime import BreakoutRealtimeAdapter
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.service import BreakoutRadarService
from app.services.breakouts.worker import BreakoutWorker
from tests.test_breakout_realtime import AT, event, publish, trade
from tests.test_breakout_service import Market, Prices, Strength, Universe


class EmptyProvider:
    @property
    def health(self) -> dict[str, object]:
        return {
            "provider": "fixture",
            "status": "active",
            "consecutive_failures": 0,
            "stale_snapshot_available": False,
        }

    async def scan(self, *, session, as_of, profile) -> DiscoverySnapshot:
        return DiscoverySnapshot(
            provider="fixture",
            status=ProviderStatus.ACTIVE,
            as_of=as_of,
            session=session,
            schema_version="fixture-v1",
            candidate_count=0,
            candidates=[],
            cache_key=f"fixture-{as_of.isoformat()}",
        )


class RecordingRadarService(BreakoutRadarService):
    received: dict[str, object]

    async def build_snapshot(self, discovery, **kwargs):
        self.received = {
            key: deepcopy(kwargs.get(key))
            for key in ("carryover_events", "previous_events", "realtime_events")
        }
        return await super().build_snapshot(discovery, **kwargs)


def test_t1_overlay_does_not_poison_next_open_session_scan(tmp_path) -> None:
    """T1 read overlays stay compatible with strict scan-input event models."""

    settings = BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=True,
        db_path=tmp_path / "radar.db",
        RANGE_PERSISTENCE_MODE="disabled",
    )
    repository = BreakoutRepository(settings.db_path, clock=lambda: AT + timedelta(minutes=5))
    repository.initialize()

    seed = event()
    seed_scan = publish(repository, AT, [seed])
    t1 = {
        "status": "pending",
        "reason": "session_incomplete",
        "version": "t1-daily-priority-v1",
        "variant": "t1_daily_priority",
        "computed_at": AT.isoformat(),
    }
    assert repository.persist_t1_evaluations(
        [{**seed, "features": {**seed["features"], "t1_priority": t1}}]
    ) == 1
    persisted_t1 = repository.overlay_t1_evaluations([seed])[0]["features"]["t1_priority"]

    # The worker must narrowly remove only its known read-side alias.  A novel
    # top-level key remains invalid at the strict domain boundary.
    with pytest.raises(ValidationError, match="unexpected_read_alias"):
        BreakoutEvent.model_validate({**seed, "unexpected_read_alias": t1})

    # A genuine quote-triggered durable row exercises the realtime overlay
    # branch alongside the T1-overlaid carryover and previous-event branches.
    realtime = BreakoutRealtimeAdapter(
        settings,
        repository,
        now=lambda: AT + timedelta(seconds=20),
    )
    assert asyncio.run(realtime.handle_trade(trade()))
    with sqlite3.connect(settings.db_path) as connection:
        row = connection.execute(
            "SELECT event_json FROM breakout_live_events WHERE event_id=?",
            (seed["event_id"],),
        ).fetchone()
        assert row is not None
        live = json.loads(row[0])
        live["features"]["t1_priority"] = persisted_t1
        live["t1_priority"] = persisted_t1
        connection.execute(
            "UPDATE breakout_live_events SET event_json=? WHERE event_id=?",
            (json.dumps(live), seed["event_id"]),
        )

    service = RecordingRadarService(
        settings,
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    result = asyncio.run(
        BreakoutWorker(
            settings,
            repository,
            provider=EmptyProvider(),
            scan_service=service,
            clock=MarketClock(now=lambda: AT + timedelta(minutes=5)),
            owner_id="t1-overlay-recovery",
        ).run_once()
    )

    assert result["status"] == "completed"
    assert result["scan_run_id"] != seed_scan
    latest = repository.latest_completed_scan()
    assert latest is not None
    assert latest["scan_run_id"] == result["scan_run_id"]
    assert [item["event_id"] for item in latest["events"]] == [seed["event_id"]]
    assert latest["events"][0]["features"]["t1_priority"]["status"] in {
        "met", "unmet", "pending", "unavailable", "not_applicable",
    }

    assert service.received["carryover_events"]
    assert service.received["previous_events"]["AAPL"]
    assert service.received["realtime_events"]
    for value in service.received["carryover_events"]:
        assert "t1_priority" not in value
        assert value["features"]["t1_priority"] == persisted_t1
    for values in service.received["previous_events"].values():
        for value in values:
            assert "t1_priority" not in value
            assert value["features"]["t1_priority"] == persisted_t1
    for value in service.received["realtime_events"]:
        assert "t1_priority" not in value
        assert value["features"]["t1_priority"] == persisted_t1
