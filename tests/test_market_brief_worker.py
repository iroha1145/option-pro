"""market_brief Worker 任务：登记、禁用原因、槽位调度、等批次、失败不降级、手动补发。

槽位判定（scheduler）与一次运行（run_brief）都属于后端库，这里一律注入假实现：
本文件只验证任务自己的决策——什么时候跑、跑完睡多久、结果怎么写回。
"""

from __future__ import annotations

import asyncio
import json
import re
import typing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

import app.services.market_brief as market_brief_package
from app.personal_config import MarketBriefConfig
from app.services.market_brief import BriefRunRecord
from app.worker.lock import ProcessFileLock
from app.worker.runtime import TaskSpec, WorkerSupervisor
from app.worker.state import WorkerStateRepository
from app.worker.tasks import (
    DEFAULT_TASK_NAMES,
    MarketBriefTask,
    build_default_tasks,
)


ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")
TRADING_DAY = date(2026, 10, 8)  # 周四，交易日
PREVIOUS_DAY = date(2026, 10, 7)
API_KEY = "sk-ant-worker-test-key-never-logged"


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


# 2026-10-08 的两个窗口与次日开盘前窗口（与默认配置一致：08:40 开窗、收盘后
# 30 分钟开窗、兜底 23:30、宽限 150 分钟）。
PRE_OPEN = (_et(TRADING_DAY, 8, 40), _et(TRADING_DAY, 11, 10), TRADING_DAY, "pre_open")
POST_CLOSE = (
    _et(TRADING_DAY, 16, 30),
    _et(date(2026, 10, 9), 2, 0),
    TRADING_DAY,
    "post_close",
)
NEXT_PRE_OPEN = (
    _et(date(2026, 10, 9), 8, 40),
    _et(date(2026, 10, 9), 11, 10),
    date(2026, 10, 9),
    "pre_open",
)
FALLBACK_AT = _et(TRADING_DAY, 23, 30)


class FakeScheduler:
    """按固定窗口表回答 due / next / fallback，替代后端库的槽判定。"""

    def __init__(self, windows=(PRE_OPEN, POST_CLOSE, NEXT_PRE_OPEN)) -> None:
        self.windows = list(windows)
        self.schedules: list[object] = []

    def due_slot(self, now, schedule):
        self.schedules.append(schedule)
        for opens, closes, trading_date, slot in self.windows:
            if opens <= now < closes:
                return trading_date, slot
        return None

    def next_slot_at(self, now, schedule):
        upcoming = [
            (opens, trading_date, slot)
            for opens, _closes, trading_date, slot in self.windows
            if opens >= now
        ]
        return min(upcoming) if upcoming else None

    def post_close_fallback_reached(self, now, trading_date, schedule):
        return now >= FALLBACK_AT


class FakeStore:
    def __init__(self, *, completed=(), runs_today: int = 0) -> None:
        self.completed_slots = set(completed)
        self.runs_today = runs_today
        self.runs_on_days: list[date] = []

    def completed(self, trading_date: date, slot: str) -> bool:
        return (trading_date, slot) in self.completed_slots

    def runs_on(self, day: date) -> int:
        self.runs_on_days.append(day)
        return self.runs_today


class FakeRunner:
    def __init__(self, *, status: str = "completed", error_code: str | None = None) -> None:
        self.status = status
        self.error_code = error_code
        self.calls: list[dict] = []

    def __call__(self, *, slot, trading_date, trigger, store, config, api_key, now):
        self.calls.append(
            {
                "slot": slot,
                "trading_date": trading_date,
                "trigger": trigger,
                "store": store,
                "config": config,
                "api_key": api_key,
            }
        )
        return BriefRunRecord(
            run_id=f"mb_{trading_date:%Y%m%d}_{slot}_{len(self.calls):08x}",
            slot=slot,
            trading_date=trading_date,
            trigger=trigger,
            status=self.status,
            started_at=now,
            completed_at=now,
            model=config.model,
            effort=config.effort,
            error_code=self.error_code,
            error_detail="原始供应商报错正文不应出现在任务状态里" if self.error_code else None,
            evidence={"blocks": ["x" * 50_000]},
            raw_output_text="y" * 60_000,
            usage={"input_tokens": 41_000, "output_tokens": 9_000},
            cost_microusd=2_154_321,
            duration_seconds=812.37,
            continuation_count=1,
        )


def _settings(*, key: str = API_KEY) -> SimpleNamespace:
    return SimpleNamespace(
        anthropic_api_key=SecretStr(key),
        market_brief_configured=bool(key.strip()),
    )


def _personal(**overrides) -> SimpleNamespace:
    return SimpleNamespace(market_brief=MarketBriefConfig(**overrides))


def _runtime(scheduled_enabled: bool = True):
    return lambda: SimpleNamespace(
        market_brief=SimpleNamespace(scheduled_enabled=scheduled_enabled)
    )


@pytest.fixture
def scheduler(monkeypatch: pytest.MonkeyPatch) -> FakeScheduler:
    fake = FakeScheduler()
    monkeypatch.setattr(market_brief_package, "due_slot", fake.due_slot)
    monkeypatch.setattr(market_brief_package, "next_slot_at", fake.next_slot_at)
    monkeypatch.setattr(
        market_brief_package,
        "post_close_fallback_reached",
        fake.post_close_fallback_reached,
    )
    return fake


def _task(
    clock: dict,
    *,
    store: FakeStore | None = None,
    runner: FakeRunner | None = None,
    settings: SimpleNamespace | None = None,
    personal: SimpleNamespace | None = None,
    eod_session=lambda: TRADING_DAY,
    scheduled_enabled: bool = True,
) -> MarketBriefTask:
    resolved_store = store or FakeStore()
    return MarketBriefTask(
        "market-brief-test",
        settings=settings or _settings(),
        personal_config=personal or _personal(),
        store_factory=lambda: resolved_store,
        runner=runner or FakeRunner(),
        now=lambda: clock["now"],
        eod_session_reader=eod_session,
        runtime_settings_reader=_runtime(scheduled_enabled),
    )


def _seconds_between(start: datetime, end: datetime) -> float:
    return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()


# ---------------------------------------------------------------------------
# 登记与漂移守卫
# ---------------------------------------------------------------------------


def test_market_brief_is_part_of_the_worker_inventory(tmp_path) -> None:
    assert "market_brief" in DEFAULT_TASK_NAMES
    # 与宏观模块的测试同款的最小设置对象：没有任何 Anthropic 字段，构造不能读它们。
    settings = SimpleNamespace(
        internal_api_token=SecretStr(""),
        macrolens_url="",
        macrolens_ca_bundle="",
        macrolens_cache_db_path=tmp_path / "catalyst-cache.db",
        macro_conditions_db_path=tmp_path / "macro-conditions.db",
        fred_api_key=SecretStr(""),
        openai_job_db_path=tmp_path / "ai-jobs.db",
        optix_worker_db_path=tmp_path / "optix-worker.db",
        optix_worker_lock_path=tmp_path / "optix-worker.lock",
        breakout_db_path=tmp_path / "optix.db",
        optix_backup_dir=tmp_path / "backups",
        personal_etl_enabled=False,
        massive_api_key="",
    )
    specs = build_default_tasks("market-brief", settings=settings)
    assert {spec.name for spec in specs} == set(DEFAULT_TASK_NAMES)
    spec = next(item for item in specs if item.name == "market_brief")
    assert isinstance(spec.runner, MarketBriefTask)
    # 定时与手动共用这一个任务，不是 manual_only。
    assert spec.manual_only is False
    assert spec.enabled is True
    assert spec.interval_seconds == 300.0
    assert spec.timeout_seconds == 1_800.0
    assert spec.honor_persisted_schedule is True
    assert spec.drain_on_shutdown is True
    assert spec.next_calendar_run_at == spec.runner.next_calendar_run_at
    # 没有 ANTHROPIC_API_KEY：报 disabled，不碰槽判定也不碰模型。
    result = asyncio.run(spec.runner())
    assert result.status == "disabled"
    assert result.error_code == "anthropic_api_key_missing"


def test_deploy_gate_expects_market_brief_but_does_not_treat_it_as_critical() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    expected_block = script.split("expected = {", 1)[1].split("}", 1)[0]
    critical_block = script.split("critical = {", 1)[1].split("}", 1)[0]
    assert "market_brief" in set(re.findall(r'"([a-z_]+)"', expected_block))
    # 缺密钥时它是 disabled；进 critical 会让每次部署都被拒。
    assert "market_brief" not in set(re.findall(r'"([a-z_]+)"', critical_block))


def test_the_action_type_and_cooldown_are_registered() -> None:
    from app.api import market_brief as market_brief_api
    from app.api import worker_actions

    assert "market_brief" in typing.get_args(worker_actions.ActionType)
    assert worker_actions._ACTION_TASKS["market_brief"] == "market_brief"
    assert worker_actions._ACTION_COOLDOWNS["market_brief"] == 600.0
    assert (
        market_brief_api.MARKET_BRIEF_COOLDOWN_SECONDS
        == worker_actions._ACTION_COOLDOWNS["market_brief"]
    )
    assert market_brief_api.MARKET_BRIEF_ACTION_TYPE == "market_brief"
    assert market_brief_api.MARKET_BRIEF_TASK_NAME == "market_brief"


# ---------------------------------------------------------------------------
# 禁用与暂停：Worker 保持健康
# ---------------------------------------------------------------------------


def test_a_missing_key_disables_the_task_without_touching_the_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("未配置密钥时不该计算槽位")

    monkeypatch.setattr(market_brief_package, "due_slot", must_not_run)
    monkeypatch.setattr(market_brief_package, "next_slot_at", must_not_run)
    runner = FakeRunner()
    task = _task(
        {"now": _et(TRADING_DAY, 8, 45)},
        runner=runner,
        settings=_settings(key=""),
    )
    result = asyncio.run(task())
    assert result.status == "disabled"
    assert result.error_code == "anthropic_api_key_missing"
    assert result.details == {"result": "disabled", "reason": "anthropic_api_key_missing"}
    assert result.next_delay_seconds == 3_600.0
    assert runner.calls == []


def test_a_settings_object_without_anthropic_fields_reads_as_unconfigured() -> None:
    task = MarketBriefTask(
        "market-brief",
        settings=SimpleNamespace(),
        personal_config=_personal(),
    )
    result = asyncio.run(task())
    assert result.status == "disabled"
    assert result.error_code == "anthropic_api_key_missing"


def test_the_task_is_disabled_by_configuration(scheduler: FakeScheduler) -> None:
    runner = FakeRunner()
    task = _task(
        {"now": _et(TRADING_DAY, 8, 45)},
        runner=runner,
        personal=_personal(enabled=False),
    )
    result = asyncio.run(task())
    assert result.status == "disabled"
    assert result.error_code == "market_brief_disabled"
    assert runner.calls == []


def test_a_disabled_market_brief_does_not_make_the_worker_unhealthy(tmp_path) -> None:
    repository = WorkerStateRepository(tmp_path / "optix-worker.db")
    observed = datetime.now(timezone.utc)
    repository.initialize(now=observed)
    token = repository.acquire("market-brief-health", lease_seconds=300, now=observed)
    assert token is not None
    for name in DEFAULT_TASK_NAMES:
        repository.record_task(
            "market-brief-health",
            token,
            name,
            enabled=True,
            status="disabled" if name == "market_brief" else "idle",
            error_code="anthropic_api_key_missing" if name == "market_brief" else None,
            now=observed,
        )
    health = repository.health(expected_tasks=DEFAULT_TASK_NAMES, now=observed)
    assert health["task_inventory_complete"] is True
    assert health["healthy"] is True
    assert health["status"] == "ok"


def test_the_owner_switch_pauses_scheduled_runs_until_the_next_slot(
    scheduler: FakeScheduler,
) -> None:
    runner = FakeRunner()
    clock = {"now": _et(TRADING_DAY, 8, 45)}
    task = _task(clock, runner=runner, scheduled_enabled=False)
    result = asyncio.run(task())
    assert result.status == "paused"
    assert result.details == {"result": "paused", "reason": "scheduled_disabled"}
    assert result.next_delay_seconds == pytest.approx(
        _seconds_between(clock["now"], POST_CLOSE[0])
    )
    assert runner.calls == []


# ---------------------------------------------------------------------------
# 槽位调度
# ---------------------------------------------------------------------------


def test_outside_every_window_the_task_sleeps_until_the_next_slot(
    scheduler: FakeScheduler,
) -> None:
    runner = FakeRunner()
    clock = {"now": _et(TRADING_DAY, 7, 0)}
    result = asyncio.run(_task(clock, runner=runner)())
    assert result.status == "idle"
    assert result.details == {"result": "no_slot_due"}
    assert result.next_delay_seconds == pytest.approx(100 * 60)
    assert runner.calls == []


AFTER_LONG_WEEKEND = (
    _et(date(2026, 10, 12), 8, 40),
    _et(date(2026, 10, 12), 11, 10),
    date(2026, 10, 12),
    "pre_open",
)


@pytest.mark.parametrize(
    ("now", "windows", "expected"),
    [
        # 下一槽只差 10 秒：至少睡 60 秒，不空转。
        (_et(TRADING_DAY, 8, 39).replace(second=50), [PRE_OPEN], 60.0),
        # 几天后才有下一槽：一次最多睡一天，醒来重算。
        (_et(TRADING_DAY, 12, 0), [AFTER_LONG_WEEKEND], 86_400.0),
        # 算不出任何槽：一小时后再看。
        (_et(TRADING_DAY, 12, 0), [], 3_600.0),
    ],
)
def test_the_idle_delay_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    now: datetime,
    windows: list,
    expected: float,
) -> None:
    fake = FakeScheduler(windows)
    monkeypatch.setattr(market_brief_package, "due_slot", lambda *_args: None)
    monkeypatch.setattr(market_brief_package, "next_slot_at", fake.next_slot_at)
    result = asyncio.run(_task({"now": now})())
    assert result.next_delay_seconds == pytest.approx(expected)


def test_a_due_pre_open_slot_runs_once_and_rearms_on_the_next_slot(
    scheduler: FakeScheduler,
) -> None:
    runner = FakeRunner()
    store = FakeStore()
    clock = {"now": _et(TRADING_DAY, 8, 45)}
    task = _task(clock, runner=runner, store=store)
    result = asyncio.run(task())

    assert result.status == "idle"
    assert result.error_code is None
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert (call["slot"], call["trading_date"], call["trigger"]) == (
        "pre_open",
        TRADING_DAY,
        "scheduled",
    )
    assert call["store"] is store
    assert call["api_key"] == API_KEY
    assert call["config"] == MarketBriefConfig().to_run_config()
    assert result.details == {
        "result": "ran",
        "run_id": "mb_20261008_pre_open_00000001",
        "slot": "pre_open",
        "trading_date": "2026-10-08",
        "trigger": "scheduled",
        "status": "completed",
        "error_code": None,
        "cost_usd": 2.1543,
        "duration_seconds": 812.4,
        "continuation_count": 1,
    }
    assert result.next_delay_seconds == pytest.approx(
        _seconds_between(clock["now"], POST_CLOSE[0])
    )


def test_a_completed_slot_is_not_run_again(scheduler: FakeScheduler) -> None:
    runner = FakeRunner()
    store = FakeStore(completed={(TRADING_DAY, "pre_open")})
    result = asyncio.run(_task({"now": _et(TRADING_DAY, 9, 0)}, runner=runner, store=store)())
    assert result.status == "idle"
    assert result.details == {
        "result": "slot_completed",
        "slot": "pre_open",
        "trading_date": "2026-10-08",
    }
    assert runner.calls == []


def test_post_close_waits_for_the_same_day_eod_batch(scheduler: FakeScheduler) -> None:
    runner = FakeRunner()
    clock = {"now": _et(TRADING_DAY, 17, 0)}
    for served in (lambda: PREVIOUS_DAY, lambda: None):
        waiting = asyncio.run(_task(clock, runner=runner, eod_session=served)())
        assert waiting.status == "idle"
        assert waiting.details["result"] == "waiting"
        assert waiting.details["waiting"] == "eod_batch"
        assert waiting.details["slot"] == "post_close"
        assert waiting.next_delay_seconds == 300.0
    assert runner.calls == []

    ready = asyncio.run(_task(clock, runner=runner, eod_session=lambda: TRADING_DAY)())
    assert ready.details["result"] == "ran"
    assert [call["slot"] for call in runner.calls] == ["post_close"]
    assert ready.next_delay_seconds == pytest.approx(
        _seconds_between(clock["now"], NEXT_PRE_OPEN[0])
    )


def test_the_default_batch_reader_is_the_evidence_helper(
    scheduler: FakeScheduler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.market_brief import evidence

    monkeypatch.setattr(evidence, "eod_batch_served_session", lambda: PREVIOUS_DAY)
    runner = FakeRunner()
    task = MarketBriefTask(
        "market-brief-test",
        settings=_settings(),
        personal_config=_personal(),
        store_factory=FakeStore,
        runner=runner,
        now=lambda: _et(TRADING_DAY, 17, 0),
        runtime_settings_reader=_runtime(),
    )
    result = asyncio.run(task())
    assert result.details["waiting"] == "eod_batch"
    assert result.details["eod_session"] == "2026-10-07"
    assert runner.calls == []


def test_post_close_stops_waiting_after_the_fallback_time(scheduler: FakeScheduler) -> None:
    runner = FakeRunner()
    clock = {"now": _et(TRADING_DAY, 23, 31)}
    result = asyncio.run(_task(clock, runner=runner, eod_session=lambda: PREVIOUS_DAY)())
    assert result.details["result"] == "ran"
    assert runner.calls[0]["slot"] == "post_close"
    assert runner.calls[0]["trading_date"] == TRADING_DAY


# ---------------------------------------------------------------------------
# 失败：不降级、不自动重跑；只有程序异常降级
# ---------------------------------------------------------------------------


def test_a_failed_model_run_stays_idle_and_is_not_retried_in_the_window(
    scheduler: FakeScheduler,
) -> None:
    runner = FakeRunner(status="failed", error_code="provider_server_error")
    clock = {"now": _et(TRADING_DAY, 8, 45)}
    task = _task(clock, runner=runner)
    first = asyncio.run(task())
    assert first.status == "idle"
    assert first.error_code == "provider_server_error"
    assert first.details["status"] == "failed"
    assert first.details["error_code"] == "provider_server_error"
    assert first.next_delay_seconds == pytest.approx(
        _seconds_between(clock["now"], POST_CLOSE[0])
    )

    clock["now"] = _et(TRADING_DAY, 9, 30)
    second = asyncio.run(task())
    assert second.status == "idle"
    assert second.details == {
        "result": "slot_attempted",
        "slot": "pre_open",
        "trading_date": "2026-10-08",
    }
    assert len(runner.calls) == 1


def test_a_program_error_degrades_without_a_paid_retry(scheduler: FakeScheduler) -> None:
    class Exploding:
        calls = 0

        def __call__(self, **_kwargs):
            Exploding.calls += 1
            raise RuntimeError("bug after the provider call")

    clock = {"now": _et(TRADING_DAY, 8, 45)}
    task = MarketBriefTask(
        "market-brief-test",
        settings=_settings(),
        personal_config=_personal(),
        store_factory=FakeStore,
        runner=Exploding(),
        now=lambda: clock["now"],
        runtime_settings_reader=_runtime(),
    )
    result = asyncio.run(task())
    assert result.status == "degraded"
    assert result.error_code == "market_brief_run_failed"
    assert result.details["error_type"] == "RuntimeError"
    assert "bug after the provider call" not in json.dumps(result.details)
    # 降级也直接排到下一槽，不走 5 秒起步的失败退避（那会在窗口内反复花钱）。
    assert result.next_delay_seconds == pytest.approx(
        _seconds_between(clock["now"], POST_CLOSE[0])
    )
    again = asyncio.run(task())
    assert again.details["result"] == "slot_attempted"
    assert Exploding.calls == 1


def test_details_stay_small_and_carry_no_secret_or_raw_output(
    scheduler: FakeScheduler,
) -> None:
    runner = FakeRunner(status="failed", error_code="schema_validation_failed")
    result = asyncio.run(_task({"now": _et(TRADING_DAY, 8, 45)}, runner=runner)())
    encoded = json.dumps(result.details, ensure_ascii=False).encode("utf-8")
    assert len(encoded) < 2_048
    serialized = encoded.decode("utf-8")
    assert API_KEY not in serialized
    assert "xxxxxxxx" not in serialized and "yyyyyyyy" not in serialized
    assert "原始供应商报错正文" not in serialized
    # 动作表拒收含 token 的键名；状态与动作结果共用这份摘要。
    assert not any("token" in key for key in result.details)


def test_a_long_or_malformed_error_code_is_narrowed(scheduler: FakeScheduler) -> None:
    runner = FakeRunner(status="failed", error_code="Provider Refusal: cyber")
    result = asyncio.run(_task({"now": _et(TRADING_DAY, 8, 45)}, runner=runner)())
    assert result.error_code == "market_brief_run_failed"
    assert result.details["error_code"] == "market_brief_run_failed"


# ---------------------------------------------------------------------------
# 手动补发
# ---------------------------------------------------------------------------


def _action(slot: str | None = None, request_id: str = "act_" + "1" * 32) -> dict:
    details = {"parameters": {"slot": slot}} if slot else {}
    return {"request_id": request_id, "action_type": "market_brief", "details": details}


def test_a_manual_run_ignores_a_completed_slot(scheduler: FakeScheduler) -> None:
    runner = FakeRunner()
    store = FakeStore(completed={(TRADING_DAY, "pre_open")})
    clock = {"now": _et(TRADING_DAY, 9, 0)}
    result = asyncio.run(
        _task(clock, runner=runner, store=store).run_for_actions([_action("pre_open")])
    )
    assert [call["trigger"] for call in runner.calls] == ["manual"]
    assert runner.calls[0]["slot"] == "pre_open"
    assert result.status == "idle"
    assert result.details["trigger"] == "manual"
    completion = result.details["action_completions"]
    assert completion == [
        {
            "request_id": "act_" + "1" * 32,
            "succeeded": True,
            "error_code": None,
            "result": {key: value for key, value in result.details.items() if key != "action_completions"},
        }
    ]
    # 次数按 UTC 日历日计。
    assert store.runs_on_days == [date(2026, 10, 8)]


def test_the_daily_limit_rejects_manual_runs_with_a_specific_code(
    scheduler: FakeScheduler,
) -> None:
    runner = FakeRunner()
    store = FakeStore(runs_today=6)
    # 美东 20:30 已是 UTC 次日 00:30：限额看的是 UTC 日。
    clock = {"now": _et(TRADING_DAY, 20, 30)}
    result = asyncio.run(
        _task(clock, runner=runner, store=store).run_for_actions([_action("post_close")])
    )
    assert runner.calls == []
    assert store.runs_on_days == [date(2026, 10, 9)]
    assert result.status == "idle"
    assert result.error_code == "daily_run_limit_reached"
    assert result.details["daily_runs"] == 6
    assert result.details["daily_max_runs"] == 6
    (completion,) = result.details["action_completions"]
    assert completion["succeeded"] is False
    assert completion["error_code"] == "daily_run_limit_reached"


@pytest.mark.parametrize(
    ("now", "expected_slot", "expected_date"),
    [
        (_et(TRADING_DAY, 10, 0), "pre_open", TRADING_DAY),
        (_et(TRADING_DAY, 13, 0), "post_close", TRADING_DAY),
        # 周六补发：交易日取上一个交易日（周五）。
        (_et(date(2026, 10, 10), 15, 0), "post_close", date(2026, 10, 9)),
    ],
)
def test_the_default_manual_slot_follows_the_new_york_clock(
    scheduler: FakeScheduler,
    now: datetime,
    expected_slot: str,
    expected_date: date,
) -> None:
    runner = FakeRunner()
    asyncio.run(_task({"now": now}, runner=runner).run_for_actions([_action()]))
    assert (runner.calls[0]["slot"], runner.calls[0]["trading_date"]) == (
        expected_slot,
        expected_date,
    )


def test_a_failed_manual_run_settles_the_action_as_failed(scheduler: FakeScheduler) -> None:
    runner = FakeRunner(status="failed", error_code="provider_refusal")
    result = asyncio.run(
        _task({"now": _et(TRADING_DAY, 9, 0)}, runner=runner).run_for_actions(
            [_action("pre_open")]
        )
    )
    # 任务本身不降级，动作表如实记失败。
    assert result.status == "idle"
    (completion,) = result.details["action_completions"]
    assert completion["succeeded"] is False
    assert completion["error_code"] == "provider_refusal"


def test_an_invalid_manual_slot_is_rejected_without_a_run(scheduler: FakeScheduler) -> None:
    runner = FakeRunner()
    result = asyncio.run(
        _task({"now": _et(TRADING_DAY, 9, 0)}, runner=runner).run_for_actions(
            [{"request_id": "act_" + "2" * 32, "details": {"parameters": {"slot": "midday"}}}]
        )
    )
    assert runner.calls == []
    (completion,) = result.details["action_completions"]
    assert completion["error_code"] == "invalid_parameters"
    assert completion["succeeded"] is False


def test_a_manual_wake_inside_an_unrun_window_hands_back_to_the_schedule(
    scheduler: FakeScheduler,
) -> None:
    runner = FakeRunner()
    clock = {"now": _et(TRADING_DAY, 8, 50)}
    task = _task(clock, runner=runner)
    # 手动补的是收盘后那份；开盘前窗口开着且还没跑，一分钟后由定时路径补上。
    manual = asyncio.run(task.run_for_actions([_action("post_close")]))
    assert manual.next_delay_seconds == 60.0
    scheduled = asyncio.run(task())
    assert [call["trigger"] for call in runner.calls] == ["manual", "scheduled"]
    assert runner.calls[1]["slot"] == "pre_open"
    assert scheduled.next_delay_seconds == pytest.approx(
        _seconds_between(clock["now"], POST_CLOSE[0])
    )

    # 定时路径在这个窗口里已经动过手：再次手动唤醒后直接睡到下一槽。
    again = asyncio.run(task.run_for_actions([_action("post_close")]))
    assert again.next_delay_seconds == pytest.approx(
        _seconds_between(clock["now"], POST_CLOSE[0])
    )


def test_a_queued_manual_brief_failure_is_recorded_on_the_action_row(
    tmp_path,
    scheduler: FakeScheduler,
) -> None:
    """走真实 Supervisor：失败的补发在动作表里是 failed，任务行仍是 idle。"""

    repository = WorkerStateRepository(tmp_path / "optix-worker.db")
    repository.initialize()
    queued = repository.request_action(
        "market_brief",
        "market_brief",
        "market_brief:test",
        cooldown_seconds=600.0,
        details={"parameters": {"slot": "pre_open"}},
    )
    runner = FakeRunner(status="failed", error_code="provider_server_error")
    task = _task({"now": _et(TRADING_DAY, 9, 0)}, runner=runner)
    supervisor = WorkerSupervisor(
        repository,
        (
            TaskSpec(
                "market_brief",
                task,
                300.0,
                timeout_seconds=1_800.0,
                honor_persisted_schedule=True,
                next_calendar_run_at=task.next_calendar_run_at,
            ),
        ),
        owner_id="market-brief-supervisor",
        process_lock=ProcessFileLock(tmp_path / "optix-worker.lock"),
    )
    payload = asyncio.run(supervisor.run_once())

    assert payload["tasks"]["market_brief"]["status"] == "idle"
    assert [call["trigger"] for call in runner.calls] == ["manual"]
    action = repository.action_request(queued["request_id"])
    assert action is not None
    assert action["status"] == "failed"
    assert action["error_code"] == "provider_server_error"
    # 失败的补发不开始冷却，Owner 可以立即再试（受每日次数上限约束）。
    assert action["cooldown_until"] is None
    (state,) = repository.task_states()
    assert state["status"] == "idle"
    assert state["error_code"] == "provider_server_error"


def test_a_successful_manual_brief_starts_the_cooldown(tmp_path, scheduler: FakeScheduler) -> None:
    repository = WorkerStateRepository(tmp_path / "optix-worker.db")
    repository.initialize()
    queued = repository.request_action(
        "market_brief",
        "market_brief",
        "market_brief:success",
        cooldown_seconds=600.0,
    )
    task = _task({"now": _et(TRADING_DAY, 13, 0)})
    supervisor = WorkerSupervisor(
        repository,
        (TaskSpec("market_brief", task, 300.0, honor_persisted_schedule=True),),
        owner_id="market-brief-success",
        process_lock=ProcessFileLock(tmp_path / "optix-worker.lock"),
    )
    asyncio.run(supervisor.run_once())
    action = repository.action_request(queued["request_id"])
    assert action is not None
    assert action["status"] == "completed"
    assert action["error_code"] is None
    assert action["cooldown_until"] is not None
    assert action["details"]["result"]["slot"] == "post_close"


def test_with_the_real_scheduler_and_store_each_slot_runs_once(tmp_path) -> None:
    """不替换槽判定与存储：真实交易日历、真实落盘，只有模型调用是假的。"""

    from app.services.market_brief import BriefStore

    store = BriefStore(tmp_path / "market-brief")
    triggers: list[tuple[str, str]] = []

    def runner(*, slot, trading_date, trigger, store, config, api_key, now):
        triggers.append((slot, trigger))
        record = BriefRunRecord(
            run_id=f"mb_{trading_date:%Y%m%d}_{slot}_{len(triggers):08x}",
            slot=slot,
            trading_date=trading_date,
            trigger=trigger,
            status="completed",
            started_at=now,
            completed_at=now,
            model=config.model,
            effort=config.effort,
            result={"output_language": "zh-CN"},
            cost_microusd=1_800_000,
            duration_seconds=640.0,
        )
        store.write_run(record)
        return record

    clock = {"now": _et(TRADING_DAY, 8, 45)}
    served = {"session": PREVIOUS_DAY}
    task = MarketBriefTask(
        "market-brief-real",
        settings=_settings(),
        personal_config=_personal(),
        store_factory=lambda: store,
        runner=runner,
        now=lambda: clock["now"],
        eod_session_reader=lambda: served["session"],
        runtime_settings_reader=_runtime(),
    )
    utc = timezone.utc

    assert asyncio.run(task()).details["result"] == "ran"
    again = asyncio.run(task())
    assert again.details["result"] == "slot_completed"
    # 下一槽：收盘后 30 分钟，美东 16:30（夏令时 = UTC 20:30）。
    assert again.next_delay_seconds == pytest.approx(7.75 * 3600)
    assert task.next_calendar_run_at(clock["now"]) == datetime(2026, 10, 8, 20, 30, tzinfo=utc)

    clock["now"] = _et(TRADING_DAY, 17, 0)
    assert asyncio.run(task()).details["waiting"] == "eod_batch"
    clock["now"] = _et(TRADING_DAY, 23, 31)
    late = asyncio.run(task())
    assert late.details["result"] == "ran"
    assert late.details["slot"] == "post_close"
    assert triggers == [("pre_open", "scheduled"), ("post_close", "scheduled")]
    assert store.completed(TRADING_DAY, "post_close") is True
    assert task.next_calendar_run_at(clock["now"]) == datetime(2026, 10, 9, 12, 40, tzinfo=utc)


# ---------------------------------------------------------------------------
# 日历回调
# ---------------------------------------------------------------------------


def test_the_calendar_callback_returns_the_next_opening(scheduler: FakeScheduler) -> None:
    task = _task({"now": _et(TRADING_DAY, 9, 0)})
    assert task.next_calendar_run_at(_et(TRADING_DAY, 9, 0)) == POST_CLOSE[0]


def test_the_calendar_callback_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supervisor 在异常处理之外调用它；抛错会让整个 Worker 进程退出。"""

    def broken(*_args):
        raise RuntimeError("scheduler bug")

    monkeypatch.setattr(market_brief_package, "next_slot_at", broken)
    task = _task({"now": _et(TRADING_DAY, 9, 0)})
    assert task.next_calendar_run_at(_et(TRADING_DAY, 9, 0)) is None


def test_the_runtime_settings_store_failure_is_degraded(scheduler: FakeScheduler) -> None:
    from app.services.runtime_settings import RuntimeSettingsStorageError

    def unreadable():
        raise RuntimeSettingsStorageError("runtime settings unreadable")

    task = MarketBriefTask(
        "market-brief-test",
        settings=_settings(),
        personal_config=_personal(),
        store_factory=FakeStore,
        runner=FakeRunner(),
        now=lambda: _et(TRADING_DAY, 8, 45),
        runtime_settings_reader=unreadable,
    )
    result = asyncio.run(task())
    assert result.status == "degraded"
    assert result.error_code == "runtime_settings_unavailable"


def test_the_task_module_exports_the_market_brief_task() -> None:
    from app.worker import tasks as worker_tasks

    assert "MarketBriefTask" in worker_tasks.__all__


def test_the_idle_delay_uses_real_elapsed_time_across_dst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-11-01 夏令时结束：墙钟差 13 小时 40 分，真实经过 14 小时 40 分。"""

    before = _et(date(2026, 10, 31), 19, 0)
    opening = _et(date(2026, 11, 1), 8, 40)
    fake = FakeScheduler([(opening, opening + timedelta(hours=2), date(2026, 11, 2), "pre_open")])
    monkeypatch.setattr(market_brief_package, "next_slot_at", fake.next_slot_at)
    assert _task({"now": before})._delay_to_next_slot(before) == pytest.approx(52_800)
