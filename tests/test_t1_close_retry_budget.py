from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.t1_priority import T1_MET, T1_PENDING
from app.worker.tasks import BreakoutTask, build_default_tasks
from tests.test_breakout_api_contract import _event, _publish
from tests.test_t1_close_completion import SESSION, _daily_frame


CLOSED_AT = datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc)


def _settings(path: Path) -> BreakoutSettings:
    return BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=True,
        db_path=path,
        scan_interval_closed_seconds=300,
        scan_interval_regular_seconds=60,
        scan_interval_premarket_seconds=60,
        worker_lease_ttl_seconds=90,
        worker_health_stale_seconds=180,
    )


def _pending_event(event_id: str, ticker: str) -> dict:
    event = _event(event_id, ticker, CLOSED_AT.replace(hour=20), 80.0)
    event["trading_date"] = SESSION
    event["setup_type"] = "DAILY_BASE_BREAKOUT"
    event["features"]["t1_priority"] = {"status": T1_PENDING, "reason": "session_incomplete"}
    event["structure"] = {"resistance_zone": {"high": 100.0, "low": 90.0}}
    return event


def _task(settings: BreakoutSettings, repository: BreakoutRepository, service, clock) -> BreakoutTask:
    task = BreakoutTask("unified")
    task._settings = settings
    task._repository = repository
    task._service = service
    task._clock = clock
    return task


def test_build_default_tasks_uses_breakout_task_entry() -> None:
    settings = SimpleNamespace(
        breakout_db_path=":memory:",
        macrolens_cache_db_path=":memory:",
        openai_job_db_path=":memory:",
        optix_worker_db_path=":memory:",
        macro_conditions_db_path=":memory:",
        optix_backup_dir=".",
    )
    specs = build_default_tasks("owner", settings=settings)
    breakout = next(spec for spec in specs if spec.name == "breakout")
    assert isinstance(breakout.runner, BreakoutTask)


def test_unified_breakout_task_persists_retry_budget_and_stops_fetching(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "t1-budget.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    _publish(repository, CLOSED_AT, [_pending_event("evt-budget", "AAA")])
    now = {"t": CLOSED_AT}
    clock = MarketClock(now=lambda: now["t"])
    price = SimpleNamespace(calls=0, tickers=[])

    async def daily(tickers, **_kwargs):
        price.calls += 1
        price.tickers.append(list(tickers))
        raise RuntimeError("daily bars not ready")

    price.daily = daily
    service = SimpleNamespace(price_data=price)
    delays = []
    attempts = []
    for _ in range(12):
        task = _task(settings, repository, service, clock)
        result = asyncio.run(task())
        assert result.status == "paused"
        delays.append(result.next_delay_seconds)
        completion = (result.details or {}).get("t1_retry_after_seconds")
        attempts.append(completion)
        now["t"] = now["t"] + timedelta(seconds=float(result.next_delay_seconds or 0) + 0.01)
    assert price.calls == 8
    assert delays[0] == 30.0
    assert all(delay <= 300 for delay in delays)
    leftover = _task(settings, repository, service, clock)
    asyncio.run(leftover())
    assert price.calls == 8
    states = repository.load_t1_retry_states(["evt-budget|2026-09-14|t1_daily_priority"])
    assert states["evt-budget|2026-09-14|t1_daily_priority"]["exhausted"] is True


def test_retry_budget_does_not_inherit_to_next_day_event(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "t1-nextday.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    _publish(repository, CLOSED_AT, [_pending_event("evt-old", "AAA")])
    now = {"t": CLOSED_AT}
    clock = MarketClock(now=lambda: now["t"])
    price = SimpleNamespace(calls=0)

    async def daily(_tickers, **_kwargs):
        price.calls += 1
        raise RuntimeError("daily bars not ready")

    price.daily = daily
    service = SimpleNamespace(price_data=price)
    for _ in range(8):
        asyncio.run(_task(settings, repository, service, clock)())
        now["t"] = now["t"] + timedelta(seconds=301)
    assert price.calls == 8
    next_day = datetime(2026, 9, 15, 21, 0, tzinfo=timezone.utc)
    new_event = _pending_event("evt-new", "BBB")
    new_event["trading_date"] = date(2026, 9, 15)
    _publish(repository, next_day, [new_event])
    now["t"] = next_day
    asyncio.run(_task(settings, repository, service, clock)())
    assert price.calls == 9


def test_partial_completion_retries_only_unfinished_subset(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "t1-partial.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    _publish(
        repository,
        CLOSED_AT,
        [_pending_event("evt-done", "AAA"), _pending_event("evt-wait", "BBB")],
    )
    now = {"t": CLOSED_AT}
    clock = MarketClock(now=lambda: now["t"])
    frame = _daily_frame()
    price = SimpleNamespace(calls=0, last=None)

    async def daily(tickers, **_kwargs):
        price.calls += 1
        price.last = list(tickers)
        if "BBB" in tickers and price.calls == 1:
            return {"AAA": SimpleNamespace(frame=frame)}
        if price.calls == 1:
            return {ticker: SimpleNamespace(frame=frame) for ticker in tickers}
        return {ticker: SimpleNamespace(frame=frame) for ticker in tickers}

    price.daily = daily
    service = SimpleNamespace(price_data=price)
    first = asyncio.run(_task(settings, repository, service, clock)())
    assert first.status == "paused"
    assert price.calls == 1
    assert set(price.last) == {"AAA", "BBB"}
    overlaid = {
        item["event_id"]: item
        for item in repository.overlay_t1_evaluations(repository.latest_completed_scan()["events"])
    }
    assert overlaid["evt-done"]["t1_priority"]["status"] == T1_MET
    assert overlaid["evt-wait"]["t1_priority"]["status"] != T1_MET
    now["t"] = now["t"] + timedelta(seconds=float(first.next_delay_seconds or 30) + 0.01)
    asyncio.run(_task(settings, repository, service, clock)())
    assert price.calls == 2
    assert price.last == ["BBB"]
    finished = {
        item["event_id"]: item["t1_priority"]["status"]
        for item in repository.overlay_t1_evaluations(repository.latest_completed_scan()["events"])
    }
    assert finished["evt-done"] == T1_MET
    assert finished["evt-wait"] == T1_MET


def test_regular_session_does_not_dispatch_exhausted_close_completion(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "t1-open.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    _publish(repository, CLOSED_AT, [_pending_event("evt-hold", "AAA")])
    now = {"t": CLOSED_AT}
    clock = MarketClock(now=lambda: now["t"])
    price = SimpleNamespace(calls=0)

    async def daily(_tickers, **_kwargs):
        price.calls += 1
        raise RuntimeError("daily bars not ready")

    price.daily = daily
    service = SimpleNamespace(price_data=price)
    for _ in range(8):
        asyncio.run(_task(settings, repository, service, clock)())
        now["t"] = now["t"] + timedelta(seconds=301)
    closed_calls = price.calls
    now["t"] = datetime(2026, 9, 15, 14, 30, tzinfo=timezone.utc)
    asyncio.run(_task(settings, repository, service, clock)())
    assert price.calls == closed_calls
