"""The macro_conditions worker task: inventory, scheduling, disabled reasons, mutex."""

from __future__ import annotations

import asyncio
import datetime as dt
from datetime import timezone
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.personal_config import MacroConfig
from app.worker import tasks as worker_tasks
from app.worker.tasks import (
    DEFAULT_TASK_NAMES,
    MacroConditionsTask,
    build_default_tasks,
    seconds_until_next_et_slot,
)


ET_SLOTS = ("08:30", "18:30")


def _settings(tmp_path, *, fred: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        internal_api_token=SecretStr(""),
        macrolens_url="",
        macrolens_ca_bundle="",
        macrolens_cache_db_path=tmp_path / "catalyst-cache.db",
        macro_conditions_db_path=tmp_path / "macro-conditions.db",
        fred_api_key=SecretStr(fred),
        macro_conditions_configured=bool(fred.strip()),
        openai_job_db_path=tmp_path / "ai-jobs.db",
        optix_worker_db_path=tmp_path / "optix-worker.db",
        optix_worker_lock_path=tmp_path / "optix-worker.lock",
        breakout_db_path=tmp_path / "optix.db",
        optix_backup_dir=tmp_path / "backups",
        personal_etl_enabled=False,
        massive_api_key="",
    )


def _personal(**overrides) -> SimpleNamespace:
    from app.personal_config import get_personal_config

    base = get_personal_config()
    macro = MacroConfig(**{"refresh_times_et": list(ET_SLOTS), **overrides})
    return SimpleNamespace(
        access=base.access,
        features=base.features,
        ai=base.ai,
        catalyst=base.catalyst,
        breakout=base.breakout,
        public_home=base.public_home,
        macro=macro,
        storage=base.storage,
        catalyst_sync_enabled=base.catalyst_sync_enabled,
        catalyst_manual_enabled=base.catalyst_manual_enabled,
        catalyst_scheduled_enabled=base.catalyst_scheduled_enabled,
    )


# ---------------------------------------------------------------------------
# 72 inventory
# ---------------------------------------------------------------------------


def test_macro_conditions_is_part_of_the_worker_inventory(tmp_path) -> None:
    assert "macro_conditions" in DEFAULT_TASK_NAMES
    specs = build_default_tasks("macro", settings=_settings(tmp_path))
    assert {spec.name for spec in specs} == set(DEFAULT_TASK_NAMES)
    spec = next(item for item in specs if item.name == "macro_conditions")
    assert isinstance(spec.runner, MacroConditionsTask)
    # Scheduled, not manual-only: one task serves both scheduled and manual runs.
    assert spec.manual_only is False
    assert spec.timeout_seconds == 1_800.0
    assert spec.enabled is True


def test_the_task_is_disabled_by_configuration(tmp_path) -> None:
    task = MacroConditionsTask(
        "macro",
        settings=_settings(tmp_path, fred="a" * 32),
        personal_config=_personal(enabled=False),
        now=lambda: dt.datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
    )
    result = asyncio.run(task())
    assert result.status == "disabled"
    assert result.details["reason"] == "macro_disabled"
    assert result.error_code is None


# ---------------------------------------------------------------------------
# 64-65 missing key keeps the worker healthy
# ---------------------------------------------------------------------------


def test_a_missing_fred_key_disables_the_task_with_a_specific_reason(tmp_path) -> None:
    task = MacroConditionsTask(
        "macro",
        settings=_settings(tmp_path, fred=""),
        personal_config=_personal(),
        now=lambda: dt.datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
    )
    result = asyncio.run(task())
    assert result.status == "disabled"
    assert result.details["reason"] == "fred_api_key_missing"


def test_a_disabled_macro_task_does_not_make_the_worker_unhealthy(tmp_path) -> None:
    from app.worker.state import WorkerStateRepository

    repository = WorkerStateRepository(tmp_path / "optix-worker.db")
    observed = dt.datetime.now(timezone.utc)
    repository.initialize(now=observed)
    token = repository.acquire("macro-health", lease_seconds=300, now=observed)
    assert token is not None
    for name in DEFAULT_TASK_NAMES:
        repository.record_task(
            "macro-health",
            token,
            name,
            enabled=True,
            status="disabled" if name == "macro_conditions" else "idle",
            now=observed,
        )
    health = repository.health(expected_tasks=DEFAULT_TASK_NAMES, now=observed)
    assert health["task_inventory_complete"] is True
    assert health["healthy"] is True
    assert health["status"] == "ok"


# ---------------------------------------------------------------------------
# 66 absolute ET scheduling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "local_hour,local_minute,expected_minutes",
    [
        (7, 0, 90),      # before the 08:30 slot
        (8, 30, 600),    # exactly on it → next is 18:30
        (12, 0, 390),    # afternoon → 18:30
        (19, 0, 13 * 60 + 30),  # after the last slot → tomorrow 08:30
        (23, 59, 8 * 60 + 31),
    ],
)
def test_the_next_run_is_an_absolute_new_york_wall_clock_slot(
    local_hour: int,
    local_minute: int,
    expected_minutes: int,
) -> None:
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    now = dt.datetime(2026, 7, 24, local_hour, local_minute, tzinfo=eastern)
    delay = seconds_until_next_et_slot(now, ET_SLOTS)
    assert delay == pytest.approx(expected_minutes * 60, abs=1)


def test_scheduling_survives_a_daylight_saving_transition() -> None:
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    # 2026-11-01 is the US autumn transition; clocks go back one hour at 02:00.
    before = dt.datetime(2026, 10, 31, 19, 0, tzinfo=eastern)
    delay = seconds_until_next_et_slot(before, ET_SLOTS)
    # Advance in UTC: adding a timedelta to an aware datetime is wall-clock
    # arithmetic, so the check has to leave the zone to prove real elapsed time.
    target = (
        before.astimezone(timezone.utc) + dt.timedelta(seconds=delay)
    ).astimezone(eastern)
    assert (target.hour, target.minute) == (8, 30)
    assert target.date() == dt.date(2026, 11, 1)
    # Real elapsed time is 14.5 hours because the day gained an hour.
    assert delay == pytest.approx(14.5 * 3600, abs=1)


def test_an_empty_slot_list_falls_back_to_a_bounded_delay() -> None:
    now = dt.datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
    assert seconds_until_next_et_slot(now, ()) == 3_600.0
    assert seconds_until_next_et_slot(now, ("bogus", "99:99")) == 3_600.0


def test_every_result_rearms_on_the_next_slot(tmp_path) -> None:
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    task = MacroConditionsTask(
        "macro",
        settings=_settings(tmp_path, fred=""),
        personal_config=_personal(),
        now=lambda: dt.datetime(2026, 7, 24, 7, 0, tzinfo=eastern),
    )
    result = asyncio.run(task())
    assert result.next_delay_seconds == pytest.approx(90 * 60, abs=1)


# ---------------------------------------------------------------------------
# 67-70 manual action, idempotency, cooldown, mutual exclusion
# ---------------------------------------------------------------------------


class _RecordingService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def refresh(self, *, trigger: str) -> dict:
        self.calls.append(trigger)
        return {
            "run_id": "mcr_test",
            "status": "succeeded",
            "published": True,
            "series_succeeded": 24,
            "series_failed": 0,
            "etf_failed": [],
            "composite_score": 51.4,
            "valid_module_count": 7,
            "data_through": "2026-07-23",
            "warnings": [],
            "error_codes": [],
        }


def test_a_manual_action_and_the_schedule_share_one_task(tmp_path) -> None:
    service = _RecordingService()
    task = MacroConditionsTask(
        "macro",
        settings=_settings(tmp_path, fred="a" * 32),
        personal_config=_personal(),
        service_factory=lambda: service,
        now=lambda: dt.datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
    )
    scheduled = asyncio.run(task())
    manual = asyncio.run(task.run_for_actions([{"request_id": "act_1"}]))
    assert service.calls == ["scheduled", "manual"]
    assert scheduled.status == "idle"
    assert manual.status == "idle"
    assert manual.details["trigger"] == "manual"
    assert manual.details["published"] is True
    assert manual.details["composite_score"] == 51.4


def test_the_action_type_and_cooldown_are_registered(tmp_path) -> None:
    from app.api import worker_actions

    assert "macro_conditions" in worker_actions._ACTION_TASKS
    assert worker_actions._ACTION_TASKS["macro_conditions"] == "macro_conditions"
    assert worker_actions._ACTION_COOLDOWNS["macro_conditions"] == 300.0


def test_only_one_macro_refresh_runs_at_a_time(tmp_path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class _Blocking:
        def __init__(self) -> None:
            self.concurrent = 0
            self.peak = 0

        async def refresh(self, *, trigger: str) -> dict:
            self.concurrent += 1
            self.peak = max(self.peak, self.concurrent)
            started.set()
            await release.wait()
            self.concurrent -= 1
            return {
                "run_id": "mcr_block",
                "status": "succeeded",
                "published": True,
                "series_succeeded": 1,
                "series_failed": 0,
                "etf_failed": [],
                "composite_score": 50.0,
                "valid_module_count": 7,
                "data_through": "2026-07-23",
                "warnings": [],
                "error_codes": [],
            }

    service = _Blocking()
    task = MacroConditionsTask(
        "macro",
        settings=_settings(tmp_path, fred="a" * 32),
        personal_config=_personal(),
        service_factory=lambda: service,
        now=lambda: dt.datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
    )

    async def scenario() -> tuple:
        first = asyncio.create_task(task())
        await started.wait()
        second = await task.run_for_actions([{"request_id": "act_2"}])
        release.set()
        return await first, second

    first_result, second_result = asyncio.run(scenario())
    assert first_result.status == "idle"
    # The overlapping request is refused rather than run concurrently.
    assert second_result.error_code == "macro_refresh_in_progress"
    assert second_result.details["reason"] == "macro_refresh_in_progress"
    assert service.peak == 1


# ---------------------------------------------------------------------------
# 71 graceful shutdown and degraded reporting
# ---------------------------------------------------------------------------


def test_a_degraded_refresh_reports_a_safe_error_code_and_no_upstream_body(
    tmp_path,
) -> None:
    class _Degraded:
        def refresh(self, *, trigger: str) -> dict:
            return {
                "run_id": "mcr_degraded",
                "status": "degraded",
                "published": True,
                "series_succeeded": 23,
                "series_failed": 1,
                "etf_failed": ["TLT"],
                "composite_score": 48.2,
                "valid_module_count": 6,
                "data_through": "2026-07-23",
                "warnings": ["series:WALCL:fred_unavailable"],
                "error_codes": ["fred_unavailable"],
            }

    task = MacroConditionsTask(
        "macro",
        settings=_settings(tmp_path, fred="a" * 32),
        personal_config=_personal(),
        service_factory=lambda: _Degraded(),
        now=lambda: dt.datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
    )
    result = asyncio.run(task())
    assert result.status == "degraded"
    assert result.error_code == "fred_unavailable"
    assert result.details["warnings"] == ["series:WALCL:fred_unavailable"]
    serialised = repr(result.details)
    assert "api_key" not in serialised
    assert "stlouisfed" not in serialised


def test_a_cancelled_refresh_still_lets_sqlite_finish_its_transaction(tmp_path) -> None:
    """The task uses _call_local, which shields the worker thread on cancel."""

    entered = asyncio.Event()
    finished: list[bool] = []

    class _Slow:
        def refresh(self, *, trigger: str) -> dict:
            import time

            entered.set()
            time.sleep(0.2)
            finished.append(True)
            return {
                "run_id": "mcr_slow",
                "status": "succeeded",
                "published": True,
                "series_succeeded": 1,
                "series_failed": 0,
                "etf_failed": [],
                "composite_score": 50.0,
                "valid_module_count": 7,
                "data_through": "2026-07-23",
                "warnings": [],
                "error_codes": [],
            }

    task = MacroConditionsTask(
        "macro",
        settings=_settings(tmp_path, fred="a" * 32),
        personal_config=_personal(),
        service_factory=lambda: _Slow(),
        now=lambda: dt.datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
    )

    async def scenario() -> None:
        running = asyncio.create_task(task())
        await entered.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(scenario())
    # The local operation was allowed to leave its transaction boundary.
    assert finished == [True]


def test_the_task_module_exports_the_macro_task() -> None:
    assert "MacroConditionsTask" in worker_tasks.__all__
    assert "seconds_until_next_et_slot" in worker_tasks.__all__
