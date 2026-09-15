"""Exercise the production model boundary, which retains enums in Python dumps."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.models import BreakoutEvent, BreakoutSetupType, MarketSession
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.service import BreakoutRadarService
from app.services.breakouts.t1_priority import (
    T1_MET, T1_NOT_APPLICABLE, T1_PENDING, attach_t1_features,
    t1_needs_close_eval, t1_setup_applicable,
)
from app.services.breakouts.worker import BreakoutWorker
from tests.test_breakout_paused_sessions import RecordingProvider, Settings
from tests.test_t1_close_completion import SESSION, _daily_frame


def _event(setup=BreakoutSetupType.DAILY_BASE_BREAKOUT):
    observed = datetime(2026, 9, 14, 18, tzinfo=timezone.utc)
    return BreakoutEvent.model_validate({
        "event_id": "evt-enum-" + setup.value,
        "ticker": "AAA",
        "trading_date": SESSION,
        "session": MarketSession.REGULAR,
        "setup_type": setup,
        "lifecycle_state": "WATCHING",
        "event_at": observed,
        "last_seen_at": observed,
        "pivot_id": "daily-pivot-enum",
        "source_snapshot_id": "enum-snapshot",
        "structure": {
            "ticker": "AAA", "base_start": "2026-08-03", "base_end": "2026-09-11",
            "calculation_cutoff_at": observed, "base_duration_days": 20,
            "resistance_zone": {"low": 99.0, "high": 100.0},
            "pivot_price": 100.0, "pivot_id": "daily-pivot-enum",
            "pivot_touch_count": 2, "quality": 0.8, "status": "active",
        },
    })


@pytest.mark.parametrize("mode", ["python", "json"])
@pytest.mark.parametrize("hour,status", [(18, T1_PENDING), (21, T1_MET)])
def test_real_event_dump_enters_daily_t1(mode, hour, status):
    event = _event()
    payload = event.model_dump(mode=mode)
    if mode == "python":
        assert payload["setup_type"] is BreakoutSetupType.DAILY_BASE_BREAKOUT
    attached = attach_t1_features(
        payload, _daily_frame(),
        as_of=datetime(2026, 9, 14, hour, tzinfo=timezone.utc),
        session=MarketSession.REGULAR if hour == 18 else MarketSession.POSTMARKET,
    )
    assert attached["t1_priority"]["status"] == status
    assert t1_needs_close_eval(attached) is (status == T1_PENDING)
    assert attached["event_id"] == event.event_id


@pytest.mark.parametrize("hour,status", [(18, T1_PENDING), (21, T1_MET)])
def test_actual_service_annotation_uses_model_enum_values(hour, status):
    service = object.__new__(BreakoutRadarService)
    result = service._attach_t1_priority(
        [_event()], daily_map={"AAA": SimpleNamespace(frame=_daily_frame())},
        observed_at=datetime(2026, 9, 14, hour, tzinfo=timezone.utc),
        observed_session=MarketSession.REGULAR if hour == 18 else MarketSession.POSTMARKET,
    )
    assert isinstance(result[0], BreakoutEvent)
    assert result[0].features["t1_priority"]["status"] == status


@pytest.mark.parametrize("mode", ["python", "json"])
def test_real_orb_event_stays_outside_t1(mode):
    attached = attach_t1_features(
        _event(BreakoutSetupType.OPENING_RANGE_BREAKOUT).model_dump(mode=mode),
        _daily_frame(), as_of=datetime(2026, 9, 14, 21, tzinfo=timezone.utc),
    )
    assert attached["t1_priority"]["status"] == T1_NOT_APPLICABLE
    assert attached["t1_priority"]["setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert not t1_needs_close_eval(attached)


def test_origin_setup_enum_fallback_is_canonical():
    assert t1_setup_applicable({"origin_setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT})
    assert not t1_setup_applicable({"origin_setup_type": BreakoutSetupType.OPENING_RANGE_BREAKOUT})


@pytest.mark.parametrize("mode", ["python", "json"])
@pytest.mark.parametrize("previous_status", [T1_PENDING, T1_NOT_APPLICABLE])
def test_close_worker_finishes_real_daily_and_repairs_prior_bad_tag(tmp_path, mode, previous_status):
    settings = Settings(tmp_path / "enum-worker.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    event = _event().model_dump(mode=mode)
    event["features"]["t1_priority"] = {
        "status": previous_status,
        "reason": "setup_not_in_t1_universe" if previous_status == T1_NOT_APPLICABLE else "session_incomplete",
        "setup_type": "BreakoutSetupType.DAILY_BASE_BREAKOUT",
    }
    repository.persist_t1_evaluations([event])
    orb = _event(BreakoutSetupType.OPENING_RANGE_BREAKOUT).model_dump(mode=mode)
    orb["features"]["t1_priority"] = {"status": T1_NOT_APPLICABLE, "reason": "setup_not_in_t1_universe"}
    repository.latest_completed_scan = lambda: {"events": [event, orb]}
    requests = []

    async def daily(tickers, **kwargs):
        requests.append(tickers)
        return {"AAA": SimpleNamespace(frame=_daily_frame())}

    provider = RecordingProvider()
    worker = BreakoutWorker(
        settings, repository, provider=provider,
        scan_service=SimpleNamespace(price_data=SimpleNamespace(daily=daily)),
        clock=MarketClock(now=lambda: datetime(2026, 9, 14, 21, tzinfo=timezone.utc)),
        owner_id="enum-worker",
    )
    result = asyncio.run(worker.run_once())
    assert result["status"] == "paused"
    assert requests == [["AAA"]]
    assert provider.calls == 0
    overlaid = repository.overlay_t1_evaluations([event, orb])
    assert overlaid[0]["t1_priority"]["status"] == T1_MET
    assert overlaid[1]["features"]["t1_priority"]["status"] == T1_NOT_APPLICABLE
    with repository.open_read_connection() as connection:
        evaluated = connection.execute("SELECT event_id FROM breakout_t1_current").fetchall()
    assert [row["event_id"] for row in evaluated] == [event["event_id"]]
