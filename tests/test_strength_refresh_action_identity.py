from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.algorithm_modes import EOD_LIMITED_V1
from app.services.eod_limited.store import variant_key
from app.worker.lock import ProcessFileLock
from app.worker.runtime import TaskSpec, WorkerSupervisor
from app.worker.state import WorkerStateRepository
from app.worker.tasks import StrengthRefreshTask


NOW = 1_789_000_000.0


def _payload(*, parameters: dict, ticker: str, failed: bool = False) -> dict:
    # A deliberately single-variant provider stub keeps separate group failure
    # and cancellation paths observable; full-batch reuse is tested separately.
    return {
        "status": "FAILED" if failed else "RAN",
        "publish": {"ok": not failed},
        "published_at": NOW,
        "served_session": "2026-09-18",
        "purpose": "live_eod_inference",
        "available_variants": [variant_key(parameters["profile"], parameters["horizon"])],
    }


def _wait_for_action(repository: WorkerStateRepository, request_id: str, *, status: str):
    async def wait() -> dict:
        for _ in range(200):
            action = repository.action_request(request_id)
            if action is not None and action["status"] == status:
                return action
            await asyncio.sleep(0.01)
        raise AssertionError(f"action {request_id} did not reach {status}")

    return wait()


def test_run_for_actions_keeps_per_request_completions(tmp_path: Path) -> None:
    from app.api import strength

    default = strength.strength_execution_parameters(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS))
    long_view = strength.normalize_strength_scan_parameters(
        {**default, "timeframe": "long"}
    )
    calls: list[str | None] = []

    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("horizon")
        calls.append(ranking)
        ticker = "LONGROW" if ranking == "long" else "MID"
        return _payload(parameters=kwargs, ticker=ticker)

    result = asyncio.run(
        StrengthRefreshTask(
            eod_runner=fake_scanner,
            snapshot_path=tmp_path / "strength-snapshot-v1.json",
            clock=lambda: NOW,
        ).run_for_actions(
            [
                {
                    "request_id": "act_owner",
                    "details": {
                        "parameters": default,
                        "parameters_hash": strength.strength_scan_parameters_hash(default),
                    },
                },
                {
                    "request_id": "act_long_view",
                    "details": {
                        "parameters": long_view,
                        "parameters_hash": strength.strength_scan_parameters_hash(long_view),
                    },
                },
            ]
        )
    )
    completions = {item["request_id"]: item for item in result.details["action_completions"]}
    assert completions["act_owner"]["succeeded"] is True
    assert completions["act_long_view"]["succeeded"] is True
    assert completions["act_owner"]["parameters_hash"] == strength.strength_scan_parameters_hash(
        default
    )
    assert completions["act_long_view"]["parameters_hash"] == strength.strength_scan_parameters_hash(long_view)
    assert completions["act_owner"]["parameters"].get("ranking_algorithm") == EOD_LIMITED_V1
    assert completions["act_long_view"]["parameters"]["ranking_algorithm"] == EOD_LIMITED_V1
    assert result.details["parameters"] == long_view
    assert calls == ["mid", "long"]


def test_first_group_failure_does_not_complete_later_success(tmp_path: Path) -> None:
    from app.api import strength

    default = strength.strength_execution_parameters(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS))
    long_view = strength.normalize_strength_scan_parameters(
        {**default, "timeframe": "long"}
    )

    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("horizon")
        failed = ranking != "long"
        return _payload(
            parameters=kwargs,
            ticker="LONGROW" if ranking == "long" else "MID",
            failed=failed,
        )

    result = asyncio.run(
        StrengthRefreshTask(
            eod_runner=fake_scanner,
            snapshot_path=tmp_path / "strength-snapshot-v1.json",
            clock=lambda: NOW,
        ).run_for_actions(
            [
                {"request_id": "act_owner", "details": {"parameters": default}},
                {"request_id": "act_long_view", "details": {"parameters": long_view}},
            ]
        )
    )
    completions = {item["request_id"]: item for item in result.details["action_completions"]}
    assert completions["act_owner"]["succeeded"] is False
    assert completions["act_owner"]["error_code"] == "eod_limited_input_unavailable"
    assert completions["act_long_view"]["succeeded"] is True
    assert completions["act_long_view"]["error_code"] is None
    assert result.status == "idle"


def test_pending_variant_failure_does_not_rewrite_owner_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import strength

    default = strength.strength_execution_parameters(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS))
    long_view = strength.normalize_strength_scan_parameters(
        {**default, "timeframe": "long"}
    )
    monkeypatch.setattr("app.services.strength.variant_demand.list_pending_strength_variant_demands", lambda: [long_view])

    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("horizon")
        return _payload(
            parameters=kwargs,
            ticker="LONGROW" if ranking == "long" else "MID",
            failed=ranking == "long",
        )

    result = asyncio.run(
        StrengthRefreshTask(
            eod_runner=fake_scanner,
            snapshot_path=tmp_path / "strength-snapshot-v1.json",
            clock=lambda: NOW,
        ).run_for_actions(
            [{"request_id": "act_owner", "details": {"parameters": default}}]
        )
    )
    assert result.status == "idle"
    assert result.error_code is None
    completions = result.details["action_completions"]
    assert len(completions) == 1
    assert completions[0]["request_id"] == "act_owner"
    assert completions[0]["succeeded"] is True
    assert completions[0]["parameters_hash"] == strength.strength_scan_parameters_hash(default)


def test_fifth_parameter_group_is_requeued_not_completed(tmp_path: Path) -> None:
    from app.api import strength

    actions = []
    for index in range(5):
        parameters = strength.normalize_strength_scan_parameters(
            {**strength.strength_execution_parameters(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)), "top": 20 + index}
        )
        actions.append(
            {
                "request_id": f"act_{index}",
                "details": {
                    "parameters": parameters,
                    "parameters_hash": strength.strength_scan_parameters_hash(parameters),
                },
            }
        )

    async def fake_scanner(**kwargs) -> dict:
        return _payload(parameters=kwargs, ticker="ROW")

    result = asyncio.run(
        StrengthRefreshTask(
            eod_runner=fake_scanner,
            snapshot_path=tmp_path / "strength-snapshot-v1.json",
            clock=lambda: NOW,
        ).run_for_actions(actions)
    )
    completed_ids = {item["request_id"] for item in result.details["action_completions"]}
    assert completed_ids == {"act_0", "act_1", "act_2", "act_3"}
    assert result.details["requeued_request_ids"] == ["act_4"]


def test_owner_and_customer_actions_keep_separate_runtime_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import strength

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    default = strength.strength_execution_parameters(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS))
    long_view = strength.normalize_strength_scan_parameters(
        {**default, "timeframe": "long"}
    )

    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("horizon")
        return _payload(
            parameters=kwargs,
            ticker="LONGROW" if ranking == "long" else "MID",
            failed=ranking != "long",
        )

    task = StrengthRefreshTask(
        eod_runner=fake_scanner,
        snapshot_path=tmp_path / "strength-snapshot-v1.json",
        clock=lambda: NOW,
    )
    repository = WorkerStateRepository(tmp_path / "identity.db")
    repository.initialize()

    async def scenario() -> None:
        supervisor = WorkerSupervisor(
            repository,
            (
                TaskSpec(
                    "strength_refresh",
                    task,
                    86_400,
                    manual_only=True,
                ),
            ),
            owner_id="identity-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "identity.lock"),
        )
        running = asyncio.create_task(supervisor.run_forever())
        for _ in range(100):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        owner = repository.request_action(
            "strength_refresh",
            "strength_refresh",
            "strength-refresh:owner-production",
            details={
                "parameters": default,
                "parameters_hash": strength.strength_scan_parameters_hash(default),
            },
        )
        customer = repository.request_action(
            "strength_variant_refresh",
            "strength_refresh",
            "strength-refresh:customer-long_view",
            details={
                "parameters": long_view,
                "parameters_hash": strength.strength_scan_parameters_hash(long_view),
            },
        )
        owner_action = await _wait_for_action(repository, owner["request_id"], status="failed")
        customer_action = await _wait_for_action(
            repository,
            customer["request_id"],
            status="completed",
        )
        assert owner_action["error_code"] == "eod_limited_input_unavailable"
        assert owner_action["details"]["result"]["parameters"].get("ranking_algorithm") == EOD_LIMITED_V1
        assert (
            owner_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(default)
        )
        assert customer_action["error_code"] is None
        assert customer_action["details"]["result"]["parameters"]["ranking_algorithm"] == EOD_LIMITED_V1
        assert (
            customer_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(long_view)
        )
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_unfinished_claimed_actions_return_to_queued(tmp_path: Path) -> None:
    from app.api import strength

    default = strength.strength_execution_parameters(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS))
    extras = [
        strength.normalize_strength_scan_parameters({**default, "top": 20 + index})
        for index in range(5)
    ]

    async def fake_scanner(**kwargs) -> dict:
        return _payload(parameters=kwargs, ticker="ROW")

    task = StrengthRefreshTask(
        eod_runner=fake_scanner,
        snapshot_path=tmp_path / "strength-snapshot-v1.json",
        clock=lambda: NOW,
    )
    repository = WorkerStateRepository(tmp_path / "requeue.db")
    repository.initialize()

    async def scenario() -> None:
        supervisor = WorkerSupervisor(
            repository,
            (
                TaskSpec(
                    "strength_refresh",
                    task,
                    86_400,
                    manual_only=True,
                ),
            ),
            owner_id="requeue-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "requeue.lock"),
        )
        running = asyncio.create_task(supervisor.run_forever())
        for _ in range(100):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        queued = [
            repository.request_action(
                "strength_refresh" if index == 0 else f"strength_variant_{index}",
                "strength_refresh",
                f"strength-refresh:top-{index}",
                details={
                    "parameters": parameters,
                    "parameters_hash": strength.strength_scan_parameters_hash(parameters),
                },
            )
            for index, parameters in enumerate(extras)
        ]
        for item, parameters in zip(queued, extras, strict=True):
            action = await _wait_for_action(repository, item["request_id"], status="completed")
            assert action["details"]["result"]["parameters"]["top"] == parameters["top"]
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def _raising_scanner(*, production: bool = False, long_view: bool = False):
    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("horizon")
        is_long_view = ranking == "long"
        if (long_view and is_long_view) or (production and not is_long_view):
            raise OSError("injected scan/write failure")
        return _payload(parameters=kwargs, ticker="LONGROW" if is_long_view else "MID")

    return fake_scanner


def _pair_actions(tmp_path: Path, scanner):
    from app.api import strength

    default = strength.strength_execution_parameters(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS))
    long_view = strength.normalize_strength_scan_parameters(
        {**default, "timeframe": "long"}
    )
    task = StrengthRefreshTask(
        eod_runner=scanner,
        snapshot_path=tmp_path / "strength-snapshot-v1.json",
        clock=lambda: NOW,
    )
    return default, long_view, task


def test_later_oserror_keeps_prior_success(tmp_path: Path) -> None:
    from app.api import strength

    default, long_view, task = _pair_actions(tmp_path, _raising_scanner(long_view=True))
    result = asyncio.run(
        task.run_for_actions(
            [
                {"request_id": "act_owner", "details": {"parameters": default}},
                {"request_id": "act_long_view", "details": {"parameters": long_view}},
            ]
        )
    )
    completions = {item["request_id"]: item for item in result.details["action_completions"]}
    assert completions["act_owner"]["succeeded"] is True
    assert completions["act_owner"]["parameters_hash"] == strength.strength_scan_parameters_hash(
        default
    )
    assert completions["act_long_view"]["succeeded"] is False
    assert completions["act_long_view"]["error_code"] == "eod_limited_input_unavailable"
    assert completions["act_long_view"]["parameters_hash"] == strength.strength_scan_parameters_hash(long_view)
    assert completions["act_long_view"]["result"]["reason"] == "OSError"


def test_earlier_oserror_lets_later_group_succeed(tmp_path: Path) -> None:
    from app.api import strength

    default, long_view, task = _pair_actions(tmp_path, _raising_scanner(production=True))
    result = asyncio.run(
        task.run_for_actions(
            [
                {"request_id": "act_owner", "details": {"parameters": default}},
                {"request_id": "act_long_view", "details": {"parameters": long_view}},
            ]
        )
    )
    completions = {item["request_id"]: item for item in result.details["action_completions"]}
    assert completions["act_owner"]["succeeded"] is False
    assert completions["act_owner"]["error_code"] == "eod_limited_input_unavailable"
    assert completions["act_long_view"]["succeeded"] is True
    assert completions["act_long_view"]["parameters"]["ranking_algorithm"] == EOD_LIMITED_V1
    assert completions["act_long_view"]["parameters_hash"] == strength.strength_scan_parameters_hash(long_view)


def test_pending_variant_oserror_keeps_owner_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import strength

    default, long_view, task = _pair_actions(tmp_path, _raising_scanner(long_view=True))
    monkeypatch.setattr("app.services.strength.variant_demand.list_pending_strength_variant_demands", lambda: [long_view])
    result = asyncio.run(
        task.run_for_actions([{"request_id": "act_owner", "details": {"parameters": default}}])
    )
    assert result.status == "idle"
    completions = result.details["action_completions"]
    assert len(completions) == 1
    assert completions[0]["request_id"] == "act_owner"
    assert completions[0]["succeeded"] is True
    assert completions[0]["parameters_hash"] == strength.strength_scan_parameters_hash(default)


def test_pending_extra_oserror_keeps_owner_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import strength
    from app.services.strength import variant_demand

    default, long_view, task = _pair_actions(tmp_path, _raising_scanner(long_view=True))
    monkeypatch.setattr(
        variant_demand,
        "list_pending_strength_variant_demands",
        lambda **_kwargs: [long_view],
    )
    result = asyncio.run(
        task.run_for_actions([{"request_id": "act_owner", "details": {"parameters": default}}])
    )
    assert result.status == "idle"
    completions = result.details["action_completions"]
    assert len(completions) == 1
    assert completions[0]["succeeded"] is True
    assert completions[0]["request_id"] == "act_owner"


def _runtime_pair(
    tmp_path: Path,
    scanner,
    *,
    timeout_seconds: float | None = None,
    grace: float = 0.05,
):
    from app.api import strength

    default, long_view, task = _pair_actions(tmp_path, scanner)
    repository = WorkerStateRepository(tmp_path / "runtime.db")
    repository.initialize()
    supervisor = WorkerSupervisor(
        repository,
        (
            TaskSpec(
                "strength_refresh",
                task,
                86_400,
                timeout_seconds=timeout_seconds,
                manual_only=True,
            ),
        ),
        owner_id="runtime-worker",
        lease_seconds=5,
        shutdown_grace_seconds=grace,
        process_lock=ProcessFileLock(tmp_path / "runtime.lock"),
    )
    return default, long_view, strength, repository, supervisor


def test_runtime_later_oserror_keeps_owner_completed(tmp_path: Path) -> None:
    default, long_view, strength, repository, supervisor = _runtime_pair(
        tmp_path,
        _raising_scanner(long_view=True),
    )

    async def scenario() -> None:
        running = asyncio.create_task(supervisor.run_forever())
        for _ in range(100):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        owner = repository.request_action(
            "strength_refresh",
            "strength_refresh",
            "strength-refresh:owner-oserror",
            details={
                "parameters": default,
                "parameters_hash": strength.strength_scan_parameters_hash(default),
            },
        )
        customer = repository.request_action(
            "strength_variant_refresh",
            "strength_refresh",
            "strength-refresh:customer-oserror",
            details={
                "parameters": long_view,
                "parameters_hash": strength.strength_scan_parameters_hash(long_view),
            },
        )
        owner_action = await _wait_for_action(repository, owner["request_id"], status="completed")
        customer_action = await _wait_for_action(repository, customer["request_id"], status="failed")
        assert owner_action["error_code"] is None
        assert (
            owner_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(default)
        )
        assert customer_action["error_code"] == "eod_limited_input_unavailable"
        assert (
            customer_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(long_view)
        )
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_runtime_timeout_keeps_first_group_and_requeues_rest(tmp_path: Path) -> None:
    hang = asyncio.Event()

    async def fake_scanner(**kwargs) -> dict:
        if kwargs.get("horizon") == "long":
            await hang.wait()
        return _payload(parameters=kwargs, ticker="MID")

    default, long_view, strength, repository, supervisor = _runtime_pair(
        tmp_path,
        fake_scanner,
        timeout_seconds=0.15,
    )

    async def scenario() -> None:
        running = asyncio.create_task(supervisor.run_forever())
        for _ in range(100):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        owner = repository.request_action(
            "strength_refresh",
            "strength_refresh",
            "strength-refresh:owner-timeout",
            details={
                "parameters": default,
                "parameters_hash": strength.strength_scan_parameters_hash(default),
            },
        )
        customer = repository.request_action(
            "strength_variant_refresh",
            "strength_refresh",
            "strength-refresh:customer-timeout",
            details={
                "parameters": long_view,
                "parameters_hash": strength.strength_scan_parameters_hash(long_view),
            },
        )
        owner_action = await _wait_for_action(repository, owner["request_id"], status="completed")
        customer_action = await _wait_for_action(repository, customer["request_id"], status="queued")
        assert (
            owner_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(default)
        )
        assert customer_action["error_code"] is None
        from datetime import datetime, timezone

        retry = (customer_action.get("details") or {}).get("retry") or {}
        assert retry.get("attempt") == 1
        assert retry.get("exhausted") is False
        assert retry.get("next_eligible_at")
        observed = datetime.now(timezone.utc)
        assert repository.has_pending_actions("strength_refresh", now=observed) is False
        assert repository.next_action_retry_delay("strength_refresh", now=observed) > 0
        supervisor.request_stop()
        hang.set()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_runtime_shutdown_keeps_settled_success(tmp_path: Path) -> None:
    hang = asyncio.Event()

    async def fake_scanner(**kwargs) -> dict:
        if kwargs.get("horizon") == "long":
            await hang.wait()
        return _payload(parameters=kwargs, ticker="MID")

    default, long_view, strength, repository, supervisor = _runtime_pair(tmp_path, fake_scanner)

    async def scenario() -> None:
        running = asyncio.create_task(supervisor.run_forever())
        for _ in range(100):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        owner = repository.request_action(
            "strength_refresh",
            "strength_refresh",
            "strength-refresh:owner-cancel",
            details={
                "parameters": default,
                "parameters_hash": strength.strength_scan_parameters_hash(default),
            },
        )
        customer = repository.request_action(
            "strength_variant_refresh",
            "strength_refresh",
            "strength-refresh:customer-cancel",
            details={
                "parameters": long_view,
                "parameters_hash": strength.strength_scan_parameters_hash(long_view),
            },
        )
        owner_action = await _wait_for_action(repository, owner["request_id"], status="completed")
        supervisor.request_stop()
        customer_action = await _wait_for_action(repository, customer["request_id"], status="queued")
        assert owner_action["error_code"] is None
        assert customer_action["status"] == "queued"
        hang.set()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_runtime_lease_loss_does_not_rewrite_settled_success(tmp_path: Path) -> None:
    default, long_view, strength, repository, supervisor = _runtime_pair(
        tmp_path,
        _raising_scanner(),
    )
    original_finish = repository.finish_actions
    finishes: list[tuple[list[str], bool | None, int]] = []

    def steal_after_first(owner_id, fencing_token, request_ids, **kwargs):
        result = original_finish(owner_id, fencing_token, request_ids, **kwargs)
        finishes.append((list(request_ids), kwargs.get("succeeded"), int(fencing_token)))
        if len(finishes) == 1:
            repository.release(owner_id, fencing_token)
            stolen = repository.acquire("thief", lease_seconds=60)
            assert stolen is not None
        return result

    repository.finish_actions = steal_after_first  # type: ignore[method-assign]

    async def scenario() -> None:
        running = asyncio.create_task(supervisor.run_forever())
        for _ in range(100):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        owner = repository.request_action(
            "strength_refresh",
            "strength_refresh",
            "strength-refresh:owner-lease",
            details={
                "parameters": default,
                "parameters_hash": strength.strength_scan_parameters_hash(default),
            },
        )
        repository.request_action(
            "strength_variant_refresh",
            "strength_refresh",
            "strength-refresh:customer-lease",
            details={
                "parameters": long_view,
                "parameters_hash": strength.strength_scan_parameters_hash(long_view),
            },
        )
        await _wait_for_action(repository, owner["request_id"], status="completed")
        try:
            await asyncio.wait_for(running, timeout=3)
        except (TimeoutError, RuntimeError, asyncio.CancelledError):
            supervisor.request_stop()
            await asyncio.wait_for(asyncio.gather(running, return_exceptions=True), timeout=2)
        final = repository.action_request(owner["request_id"])
        assert final is not None
        assert final["status"] == "completed"
        assert (
            final["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(default)
        )
        assert any(item[1] is True for item in finishes)

    asyncio.run(scenario())


def test_invalid_parameters_fail_and_valid_group_completes(tmp_path: Path) -> None:
    from app.api import strength

    default, _long_view, task = _pair_actions(tmp_path, _raising_scanner())
    result = asyncio.run(
        task.run_for_actions(
            [
                {"request_id": "act_bad", "details": {"parameters": default, "parameters_hash": "0" * 64}},
                {"request_id": "act_owner", "details": {"parameters": default}},
            ]
        )
    )
    completions = {item["request_id"]: item for item in result.details["action_completions"]}
    assert completions["act_bad"]["succeeded"] is False
    assert completions["act_bad"]["error_code"] == "invalid_parameters"
    assert completions["act_owner"]["succeeded"] is True
    assert completions["act_owner"]["parameters_hash"] == strength.strength_scan_parameters_hash(
        default
    )
    assert result.details["requeued_request_ids"] == []


def test_timeout_retry_has_delay_and_exhausts(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    from app.worker.state import (
        ACTION_TIMEOUT_RETRY_MAX,
        action_is_claimable,
        bump_action_retry,
    )

    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    details = {}
    for attempt in range(ACTION_TIMEOUT_RETRY_MAX):
        details, exhausted, next_at = bump_action_retry(details, now=now, reason="task_timeout")
        assert exhausted is False
        assert next_at is not None
        assert action_is_claimable(details, now) is False
        assert action_is_claimable(details, next_at) is True
        now = next_at
    details, exhausted, next_at = bump_action_retry(details, now=now, reason="task_timeout")
    assert exhausted is True
    assert next_at is None
    assert details["retry"]["exhausted"] is True
