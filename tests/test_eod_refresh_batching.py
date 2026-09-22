from __future__ import annotations

import asyncio

import pytest

from app.api import strength
from app.services.eod_limited import PURPOSE_LIVE
from app.services.eod_limited.store import variant_key
from app.services.research_eod_v1.constants import HORIZONS, PROFILES
from app.services.strength import variant_demand
from app.worker.runtime import TaskResult
from app.worker.tasks import StrengthRefreshTask


def parameters(profile="balanced", horizon="mid"):
    return strength.strength_execution_parameters({
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "ranking_algorithm": "eod_limited_v1", "profile": profile, "timeframe": horizon,
    })


def success():
    return {
        "status": "RAN", "purpose": PURPOSE_LIVE,
        "served_session": "2026-09-18", "published_at": 1_800_000_000.0,
        "publish": {"ok": True},
        "available_variants": [variant_key(p, h) for p in PROFILES for h in HORIZONS],
    }


def actions(variants):
    return [{"request_id": str(i), "details": {"parameters": item}}
            for i, item in enumerate(variants)]


@pytest.fixture
def isolated_demands(monkeypatch):
    completed = []
    monkeypatch.setattr(strength, "scheduled_strength_scan_parameters", parameters)
    monkeypatch.setattr(strength, "list_recent_strength_variant_parameters", lambda *args, **kwargs: [])
    monkeypatch.setattr(strength, "a0_companion_for_admin_default", lambda *args: None)
    monkeypatch.setattr(variant_demand, "list_pending_strength_variant_demands", lambda: [])
    monkeypatch.setattr(variant_demand, "complete_strength_variant_demand",
                        lambda params, **kwargs: completed.append((params, kwargs)))
    return completed


def test_scheduled_default_and_pending_share_one_published_batch(monkeypatch, isolated_demands):
    pending = [parameters(p, h) for p, h in [
        ("conservative", "short"), ("aggressive", "short"),
        ("conservative", "long"), ("aggressive", "long"),
    ]]
    monkeypatch.setattr(variant_demand, "list_pending_strength_variant_demands", lambda: pending)
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        return success()

    result = asyncio.run(StrengthRefreshTask(eod_runner=runner)())
    assert result.status == "idle"
    assert len(calls) == 1
    assert calls[0]["all_variants"] is True
    assert result.details["variant_refresh_published"] == 4
    assert [item for item, _ in isolated_demands] == pending
    assert all(state["status"] == "completed" for _, state in isolated_demands)


def test_scheduled_demand_read_failure_is_visible_and_retried(monkeypatch, isolated_demands):
    calls = []
    reader_calls = 0

    def read_demands():
        nonlocal reader_calls
        reader_calls += 1
        if reader_calls == 1:
            raise OSError("unavailable")
        return []

    monkeypatch.setattr(variant_demand, "list_pending_strength_variant_demands", read_demands)
    task = StrengthRefreshTask(eod_runner=lambda **kwargs: calls.append(kwargs) or success())

    first = asyncio.run(task())
    assert first.status == "degraded"
    assert first.error_code == "strength_variant_demand_read_failed"
    assert first.details["variant_refresh_errors"] == [{
        "parameters_hash": None,
        "error_code": "strength_variant_demand_read_failed",
        "reason": "OSError",
    }]
    assert first.details["variant_refresh_attempted"] == 0
    assert 0 < first.next_delay_seconds <= 30

    recovered = asyncio.run(task())
    assert recovered.status == "idle"
    assert recovered.details["variant_refresh_errors"] == []
    assert reader_calls == 2
    assert len(calls) == 1


def test_demand_read_failure_keeps_default_error_and_shortens_retry(monkeypatch, isolated_demands):
    def failed_read():
        raise OSError("unavailable")

    monkeypatch.setattr(variant_demand, "list_pending_strength_variant_demands", failed_read)
    task = StrengthRefreshTask(eod_runner=lambda **_kwargs: success())

    async def failed_default(_parameters):
        return TaskResult(status="degraded", error_code="strength_input_unavailable")

    monkeypatch.setattr(task, "_run", failed_default)
    result = asyncio.run(task())
    assert result.status == "degraded"
    assert result.error_code == "strength_input_unavailable"
    assert result.next_delay_seconds == 30.0
    assert result.details["variant_refresh_errors"][0]["error_code"] == "strength_variant_demand_read_failed"


def test_action_groups_keep_identity_and_next_call_refreshes_again(monkeypatch, isolated_demands):
    variants = [parameters("balanced", "mid"), parameters("aggressive", "short")]
    pending = parameters("conservative", "long")
    monkeypatch.setattr(variant_demand, "list_pending_strength_variant_demands", lambda: [pending])
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        return success()

    task = StrengthRefreshTask(eod_runner=runner)
    result = asyncio.run(task.run_for_actions(actions(variants)))
    assert len(calls) == 1
    completions = result.details["action_completions"]
    assert len(completions) == 2
    assert [item for item, _ in isolated_demands] == [*variants, pending]
    for index, entry in enumerate(completions):
        assert entry["request_id"] == str(index)
        assert entry["succeeded"] is True
        assert entry["parameters"] == variants[index]
        assert entry["result"]["parameters"] == variants[index]
        assert entry["parameters_hash"] == strength.strength_scan_parameters_hash(variants[index])
        assert entry["result"]["parameters_hash"] == entry["parameters_hash"]
        assert entry["result"]["completed_at"] == "2027-01-15T08:00:00Z"
    asyncio.run(task.run_for_actions(actions(variants)))
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["exception", "unpublished", "missing_timestamp"])
def test_failed_publication_is_not_reused(failure, isolated_demands):
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            if failure == "exception":
                raise ValueError("input unavailable")
            outcome = success()
            if failure == "unpublished":
                outcome["publish"] = {"ok": False}
            else:
                outcome.pop("published_at")
            return outcome
        return success()

    result = asyncio.run(StrengthRefreshTask(eod_runner=runner).run_for_actions(actions([
        parameters(), parameters("conservative", "short"), parameters("aggressive", "long"),
    ])))
    assert len(calls) == 2
    assert [entry["succeeded"] for entry in result.details["action_completions"]] == [False, True, True]


def test_batch_missing_requested_variant_is_not_reused(isolated_demands):
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        outcome = success()
        if len(calls) == 1:
            outcome["available_variants"] = [variant_key("balanced", "mid")]
        return outcome

    result = asyncio.run(StrengthRefreshTask(eod_runner=runner).run_for_actions(actions([
        parameters(), parameters("aggressive", "long"),
    ])))
    assert len(calls) == 2
    assert all(entry["succeeded"] for entry in result.details["action_completions"])
