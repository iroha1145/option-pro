from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.algorithm_modes import A0_ALGORITHM, PRODUCTION_ALGORITHM
from app.worker.lock import ProcessFileLock
from app.worker.runtime import TaskSpec, WorkerSupervisor
from app.worker.state import WorkerStateRepository
from app.worker.tasks import StrengthRefreshTask


NOW = 1_789_000_000.0


def _payload(*, parameters: dict, ticker: str, failed: bool = False) -> dict:
    body = {
        "as_of": "2026-07-16T00:00:00+00:00",
        "params": {key: value for key, value in parameters.items() if key != "force_refresh"},
        "count": 0 if failed else 1,
        "rows": [] if failed else [{"ticker": ticker, "score": 91.0}],
        "results": [] if failed else [{"ticker": ticker, "score": 91.0}],
    }
    if failed:
        body["data_sources"] = {"prices": {"status": "unavailable"}}
    return body


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

    default = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    a0 = strength.normalize_strength_scan_parameters(
        {**default, "ranking_algorithm": A0_ALGORITHM}
    )
    calls: list[str | None] = []

    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("ranking_algorithm")
        calls.append(ranking)
        ticker = "A0ROW" if ranking == A0_ALGORITHM else "PROD"
        return _payload(parameters=kwargs, ticker=ticker)

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
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
                    "request_id": "act_a0",
                    "details": {
                        "parameters": a0,
                        "parameters_hash": strength.strength_scan_parameters_hash(a0),
                    },
                },
            ]
        )
    )
    completions = {item["request_id"]: item for item in result.details["action_completions"]}
    assert completions["act_owner"]["succeeded"] is True
    assert completions["act_a0"]["succeeded"] is True
    assert completions["act_owner"]["parameters_hash"] == strength.strength_scan_parameters_hash(
        default
    )
    assert completions["act_a0"]["parameters_hash"] == strength.strength_scan_parameters_hash(a0)
    assert completions["act_owner"]["parameters"].get("ranking_algorithm") in {
        None,
        PRODUCTION_ALGORITHM,
    }
    assert completions["act_a0"]["parameters"]["ranking_algorithm"] == A0_ALGORITHM
    assert result.details["parameters"] == a0
    assert A0_ALGORITHM in calls and any(item in {None, PRODUCTION_ALGORITHM} for item in calls)


def test_first_group_failure_does_not_complete_later_success(tmp_path: Path) -> None:
    from app.api import strength

    default = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    a0 = strength.normalize_strength_scan_parameters(
        {**default, "ranking_algorithm": A0_ALGORITHM}
    )

    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("ranking_algorithm")
        failed = ranking != A0_ALGORITHM
        return _payload(
            parameters=kwargs,
            ticker="A0ROW" if ranking == A0_ALGORITHM else "PROD",
            failed=failed,
        )

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
            snapshot_path=tmp_path / "strength-snapshot-v1.json",
            clock=lambda: NOW,
        ).run_for_actions(
            [
                {"request_id": "act_owner", "details": {"parameters": default}},
                {"request_id": "act_a0", "details": {"parameters": a0}},
            ]
        )
    )
    completions = {item["request_id"]: item for item in result.details["action_completions"]}
    assert completions["act_owner"]["succeeded"] is False
    assert completions["act_owner"]["error_code"] == "strength_input_unavailable"
    assert completions["act_a0"]["succeeded"] is True
    assert completions["act_a0"]["error_code"] is None
    assert result.status == "idle"


def test_companion_failure_does_not_rewrite_owner_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import strength

    default = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    a0 = strength.normalize_strength_scan_parameters(
        {**default, "ranking_algorithm": A0_ALGORITHM}
    )
    monkeypatch.setattr(strength, "a0_companion_for_admin_default", lambda *_args, **_kwargs: a0)

    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("ranking_algorithm")
        return _payload(
            parameters=kwargs,
            ticker="A0ROW" if ranking == A0_ALGORITHM else "PROD",
            failed=ranking == A0_ALGORITHM,
        )

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
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
            {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": 20 + index}
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
            scanner=fake_scanner,
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
    default = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    a0 = strength.normalize_strength_scan_parameters(
        {**default, "ranking_algorithm": A0_ALGORITHM}
    )

    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("ranking_algorithm")
        return _payload(
            parameters=kwargs,
            ticker="A0ROW" if ranking == A0_ALGORITHM else "PROD",
            failed=ranking != A0_ALGORITHM,
        )

    task = StrengthRefreshTask(
        scanner=fake_scanner,
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
            "strength-refresh:customer-a0",
            details={
                "parameters": a0,
                "parameters_hash": strength.strength_scan_parameters_hash(a0),
            },
        )
        owner_action = await _wait_for_action(repository, owner["request_id"], status="failed")
        customer_action = await _wait_for_action(
            repository,
            customer["request_id"],
            status="completed",
        )
        assert owner_action["error_code"] == "strength_input_unavailable"
        assert owner_action["details"]["result"]["parameters"].get("ranking_algorithm") in {
            None,
            PRODUCTION_ALGORITHM,
        }
        assert (
            owner_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(default)
        )
        assert customer_action["error_code"] is None
        assert customer_action["details"]["result"]["parameters"]["ranking_algorithm"] == A0_ALGORITHM
        assert (
            customer_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(a0)
        )
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_unfinished_claimed_actions_return_to_queued(tmp_path: Path) -> None:
    from app.api import strength

    default = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    extras = [
        strength.normalize_strength_scan_parameters({**default, "top": 20 + index})
        for index in range(5)
    ]

    async def fake_scanner(**kwargs) -> dict:
        return _payload(parameters=kwargs, ticker="ROW")

    task = StrengthRefreshTask(
        scanner=fake_scanner,
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


def _raising_scanner(*, production: bool = False, a0: bool = False):
    async def fake_scanner(**kwargs) -> dict:
        ranking = kwargs.get("ranking_algorithm")
        is_a0 = ranking == A0_ALGORITHM
        if (a0 and is_a0) or (production and not is_a0):
            raise OSError("injected scan/write failure")
        return _payload(parameters=kwargs, ticker="A0ROW" if is_a0 else "PROD")

    return fake_scanner


def _pair_actions(tmp_path: Path, scanner):
    from app.api import strength

    default = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    a0 = strength.normalize_strength_scan_parameters(
        {**default, "ranking_algorithm": A0_ALGORITHM}
    )
    task = StrengthRefreshTask(
        scanner=scanner,
        snapshot_path=tmp_path / "strength-snapshot-v1.json",
        clock=lambda: NOW,
    )
    return default, a0, task


def test_later_oserror_keeps_prior_success(tmp_path: Path) -> None:
    from app.api import strength

    default, a0, task = _pair_actions(tmp_path, _raising_scanner(a0=True))
    result = asyncio.run(
        task.run_for_actions(
            [
                {"request_id": "act_owner", "details": {"parameters": default}},
                {"request_id": "act_a0", "details": {"parameters": a0}},
            ]
        )
    )
    completions = {item["request_id"]: item for item in result.details["action_completions"]}
    assert completions["act_owner"]["succeeded"] is True
    assert completions["act_owner"]["parameters_hash"] == strength.strength_scan_parameters_hash(
        default
    )
    assert completions["act_a0"]["succeeded"] is False
    assert completions["act_a0"]["error_code"] == "task_failed"
    assert completions["act_a0"]["parameters_hash"] == strength.strength_scan_parameters_hash(a0)
    assert completions["act_a0"]["result"]["error_type"] == "OSError"


def test_earlier_oserror_lets_later_group_succeed(tmp_path: Path) -> None:
    from app.api import strength

    default, a0, task = _pair_actions(tmp_path, _raising_scanner(production=True))
    result = asyncio.run(
        task.run_for_actions(
            [
                {"request_id": "act_owner", "details": {"parameters": default}},
                {"request_id": "act_a0", "details": {"parameters": a0}},
            ]
        )
    )
    completions = {item["request_id"]: item for item in result.details["action_completions"]}
    assert completions["act_owner"]["succeeded"] is False
    assert completions["act_owner"]["error_code"] == "task_failed"
    assert completions["act_a0"]["succeeded"] is True
    assert completions["act_a0"]["parameters"]["ranking_algorithm"] == A0_ALGORITHM
    assert completions["act_a0"]["parameters_hash"] == strength.strength_scan_parameters_hash(a0)


def test_companion_oserror_keeps_owner_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import strength

    default, a0, task = _pair_actions(tmp_path, _raising_scanner(a0=True))
    monkeypatch.setattr(strength, "a0_companion_for_admin_default", lambda *_args, **_kwargs: a0)
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

    default, a0, task = _pair_actions(tmp_path, _raising_scanner(a0=True))
    monkeypatch.setattr(
        variant_demand,
        "list_pending_strength_variant_demands",
        lambda **_kwargs: [a0],
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

    default, a0, task = _pair_actions(tmp_path, scanner)
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
    return default, a0, strength, repository, supervisor


def test_runtime_later_oserror_keeps_owner_completed(tmp_path: Path) -> None:
    default, a0, strength, repository, supervisor = _runtime_pair(
        tmp_path,
        _raising_scanner(a0=True),
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
                "parameters": a0,
                "parameters_hash": strength.strength_scan_parameters_hash(a0),
            },
        )
        owner_action = await _wait_for_action(repository, owner["request_id"], status="completed")
        customer_action = await _wait_for_action(repository, customer["request_id"], status="failed")
        assert owner_action["error_code"] is None
        assert (
            owner_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(default)
        )
        assert customer_action["error_code"] == "task_failed"
        assert (
            customer_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(a0)
        )
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_runtime_timeout_keeps_first_group_and_requeues_rest(tmp_path: Path) -> None:
    hang = asyncio.Event()

    async def fake_scanner(**kwargs) -> dict:
        if kwargs.get("ranking_algorithm") == A0_ALGORITHM:
            await hang.wait()
        return _payload(parameters=kwargs, ticker="PROD")

    default, a0, strength, repository, supervisor = _runtime_pair(
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
                "parameters": a0,
                "parameters_hash": strength.strength_scan_parameters_hash(a0),
            },
        )
        owner_action = await _wait_for_action(repository, owner["request_id"], status="completed")
        customer_action = await _wait_for_action(repository, customer["request_id"], status="queued")
        assert (
            owner_action["details"]["result"]["parameters_hash"]
            == strength.strength_scan_parameters_hash(default)
        )
        assert customer_action["error_code"] is None
        supervisor.request_stop()
        hang.set()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_runtime_shutdown_keeps_settled_success(tmp_path: Path) -> None:
    hang = asyncio.Event()

    async def fake_scanner(**kwargs) -> dict:
        if kwargs.get("ranking_algorithm") == A0_ALGORITHM:
            await hang.wait()
        return _payload(parameters=kwargs, ticker="PROD")

    default, a0, strength, repository, supervisor = _runtime_pair(tmp_path, fake_scanner)

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
                "parameters": a0,
                "parameters_hash": strength.strength_scan_parameters_hash(a0),
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
    default, a0, strength, repository, supervisor = _runtime_pair(
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
                "parameters": a0,
                "parameters_hash": strength.strength_scan_parameters_hash(a0),
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
