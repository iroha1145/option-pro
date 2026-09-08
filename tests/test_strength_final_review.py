"""Final PR148 regressions; all tests exercise imported production code."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.api import strength
from app.worker.tasks import StrengthRefreshTask
from tests.test_strength_publish_guard import (
    CURRENT, PREVIOUS, DEFAULT, NOW, _multi_row_payload, _failed_payload, _write_variant,
)
from tests.test_strength_variant_lifecycle import _payload


def _pool_payload(rows, *, data_errors=0, insufficient=0, version="pool-v1"):
    payload = _multi_row_payload(DEFAULT, rows) if rows else _payload(through=CURRENT)
    if not rows:
        payload.update(rows=[], results=[], count=0)
    payload.update(
        universe_count=10, universe_version=version,
        skipped={"data_error": data_errors, "insufficient_history": insufficient,
                 "low_price": 10 - len(rows) - data_errors - insufficient, "low_liquidity": 0},
    )
    return payload


def _write(path, payload, saved_at=NOW):
    return strength._write_strength_snapshot(
        path, parameters=dict(DEFAULT), payload=payload, saved_at=saved_at,
    )


@pytest.mark.parametrize("remaining", [1, 0])
def test_filtered_result_count_is_not_provider_coverage(tmp_path, remaining):
    path = tmp_path / "strength-snapshot-v1.json"
    _write(path, _pool_payload([("AAPL", CURRENT), ("MSFT", CURRENT)]))
    rows = [("AAPL", CURRENT)] if remaining else []
    assert _write(path, _pool_payload(rows), NOW + 60) == "written"
    assert json.loads(path.read_bytes())["payload"]["count"] == remaining


def test_same_top_rows_cannot_hide_lost_pool_inputs(tmp_path):
    path = tmp_path / "strength-snapshot-v1.json"
    rows = [("AAPL", CURRENT), ("MSFT", CURRENT)]
    _write(path, _pool_payload(rows))
    before = path.read_bytes()
    assert _write(path, _pool_payload(rows, data_errors=5), NOW + 60) != "written"
    assert path.read_bytes() == before


def test_changed_universe_is_not_mistaken_for_same_pool_coverage_loss(tmp_path):
    path = tmp_path / "strength-snapshot-v1.json"
    rows = [("AAPL", CURRENT), ("MSFT", CURRENT)]
    _write(path, _pool_payload(rows))
    changed = _pool_payload([("AAPL", CURRENT)], version="pool-v2")
    changed["universe_count"] = 5
    changed["skipped"]["low_price"] = 4
    assert _write(path, changed, NOW + 60) == "written"


def test_aggregate_minimum_cannot_hide_one_symbols_regression(tmp_path):
    path = tmp_path / "strength-snapshot-v1.json"
    _write(path, _pool_payload([("AAPL", PREVIOUS), ("MSFT", CURRENT)]))
    before = path.read_bytes()
    regressed = _pool_payload([("AAPL", PREVIOUS), ("MSFT", PREVIOUS)])
    assert _write(path, regressed, NOW + 60) != "written"
    assert path.read_bytes() == before


@pytest.mark.parametrize("mixed", [False, True])
def test_future_candidate_does_not_replace_good_publication(tmp_path, mixed):
    path = tmp_path / "strength-snapshot-v1.json"
    _write(path, _pool_payload([("AAPL", CURRENT), ("MSFT", CURRENT)]))
    before = path.read_bytes()
    candidate = _pool_payload([
        ("AAPL", CURRENT if mixed else "2026-12-01T21:00:00+00:00"),
        ("MSFT", "2026-12-01T21:00:00+00:00"),
    ])
    assert _write(path, candidate, NOW + 60) != "written"
    assert path.read_bytes() == before


def test_valid_refresh_can_recover_legacy_future_input(tmp_path):
    path = tmp_path / "strength-snapshot-v1.json"
    # Simulate a snapshot persisted by an older build. Its unknown clock must
    # not become a monotonic lower bound that locks out every valid refresh.
    _write(path, _pool_payload([("AAPL", "2026-12-01T21:00:00+00:00")]))
    assert _write(path, _pool_payload([("AAPL", CURRENT)]), NOW + 60) == "written"
    assert json.loads(path.read_bytes())["payload"]["score_data_through"] == CURRENT


@pytest.mark.parametrize("corruption", ["future_saved", "wrong_parameters", "oversize"])
def test_invalid_existing_document_cannot_block_recovery(tmp_path, monkeypatch, corruption):
    path = tmp_path / "strength-snapshot-v1.json"
    monkeypatch.setattr(strength.time, "time", lambda: NOW + 120)
    _write(path, _pool_payload([("AAPL", CURRENT)]))
    doc = json.loads(path.read_bytes())
    if corruption == "future_saved":
        doc["saved_at"] = NOW + 86400
    elif corruption == "wrong_parameters":
        doc["parameters"]["profile"] = "aggressive"
    else:
        doc["padding"] = "x" * strength._STRENGTH_SNAPSHOT_MAX_BYTES
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _write(path, _pool_payload([("AAPL", CURRENT)]), NOW + 60) == "written"


def _scheduled_task(tmp_path, monkeypatch):
    base = tmp_path / "strength-snapshot-v1.json"
    clock = [NOW]
    monkeypatch.setattr(strength.time, "time", lambda: clock[0])
    bad = {**DEFAULT, "sector_id": "semiconductors"}
    good = {**DEFAULT, "sector_id": "software"}
    _write_variant(base, bad)
    _write_variant(base, good)
    recent = [bad, good]
    calls = []
    fail = [True]

    async def scan(**kwargs):
        params = {key: kwargs[key] for key in DEFAULT}
        calls.append(params)
        if fail[0] and params == bad:
            return _failed_payload(params)
        return _payload(params, through=CURRENT)

    monkeypatch.setattr(strength, "list_recent_strength_variant_parameters", lambda *_a, **_k: list(recent))
    task = StrengthRefreshTask(scanner=scan, snapshot_path=base, clock=lambda: clock[0])
    return task, clock, bad, good, recent, calls, fail


def test_manual_scan_preserves_pending_retry_deadline(tmp_path, monkeypatch):
    task, clock, bad, good, recent, calls, fail = _scheduled_task(tmp_path, monkeypatch)
    first = asyncio.run(task())
    deadline = clock[0] + first.next_delay_seconds
    clock[0] += 60
    manual = asyncio.run(task.run_for_actions([{"details": {"parameters": good}}]))
    assert manual.status == "idle"
    assert clock[0] + manual.next_delay_seconds == pytest.approx(deadline)
    clock[0] = deadline
    calls.clear()
    retry = asyncio.run(task())
    assert calls == [bad]
    assert retry.status == "degraded"
    assert clock[0] + retry.next_delay_seconds == pytest.approx(NOW + 86400)


def test_retry_pins_failed_parameters_despite_recent_list_changes(tmp_path, monkeypatch):
    task, clock, bad, good, recent, calls, fail = _scheduled_task(tmp_path, monkeypatch)
    first = asyncio.run(task())
    clock[0] += first.next_delay_seconds
    recent[:] = [good]
    calls.clear()
    second = asyncio.run(task())
    assert calls == [bad]
    assert second.status == "degraded"
    assert second.details["variant_refresh_errors"][0]["parameters_hash"] == strength.strength_scan_parameters_hash(bad)


def test_retry_does_not_repeat_successful_variants(tmp_path, monkeypatch):
    task, clock, bad, good, recent, calls, fail = _scheduled_task(tmp_path, monkeypatch)
    first = asyncio.run(task())
    clock[0] += first.next_delay_seconds
    calls.clear()
    fail[0] = False
    recovered = asyncio.run(task())
    assert calls == [bad]
    assert recovered.status == "idle"
    assert recovered.details["variant_refresh_errors"] == []
    assert recovered.next_delay_seconds == pytest.approx(86400 - first.next_delay_seconds)


def test_early_wakeup_does_not_spend_retry_or_erase_failure(tmp_path, monkeypatch):
    task, clock, bad, good, recent, calls, fail = _scheduled_task(tmp_path, monkeypatch)
    first = asyncio.run(task())
    clock[0] += 30
    calls.clear()
    early = asyncio.run(task())
    assert calls == []
    assert early.status == "degraded"
    assert early.details["variant_refresh_errors"]
    assert early.next_delay_seconds == pytest.approx(first.next_delay_seconds - 30)


def test_matching_manual_recovery_clears_pending_retry(tmp_path, monkeypatch):
    task, clock, bad, good, recent, calls, fail = _scheduled_task(tmp_path, monkeypatch)
    asyncio.run(task())
    clock[0] += 60
    fail[0] = False
    recovered = asyncio.run(task.run_for_actions([{"details": {"parameters": bad}}]))
    assert recovered.status == "idle"
    assert recovered.next_delay_seconds == pytest.approx(86400 - 60)


def test_future_row_is_unknown_even_with_a_current_pool_minimum():
    from app.services.strength.freshness import evaluate_strength_snapshot_freshness

    payload = _pool_payload([
        ("AAPL", CURRENT), ("MSFT", "2026-12-01T21:00:00+00:00"),
    ])
    verdict = evaluate_strength_snapshot_freshness(
        saved_at=NOW, payload=payload, now=NOW + 60, ttl_seconds=86400,
        scoring_version=strength.STRENGTH_SCORE_VERSION,
        expected_scoring_version=strength.STRENGTH_SCORE_VERSION,
    )
    assert verdict.source_status == "unknown"
    assert verdict.unknown_input_time is True
    assert verdict.score_data_through is None


def test_common_member_losing_its_date_cannot_borrow_the_pool_clock(tmp_path):
    path = tmp_path / "strength-snapshot-v1.json"
    _write(path, _pool_payload([("AAPL", CURRENT), ("MSFT", CURRENT)]))
    before = path.read_bytes()
    unknown = _pool_payload([("AAPL", CURRENT), ("MSFT", None)])
    assert _write(path, unknown, NOW + 60) != "written"
    assert path.read_bytes() == before


@pytest.mark.parametrize("invalid", [None, True, -1, 1.5, "0"])
def test_invalid_pool_counters_have_unknown_coverage(invalid):
    from app.services.strength.freshness import extract_usable_input_coverage

    payload = _pool_payload([("AAPL", CURRENT)])
    payload["skipped"]["data_error"] = invalid
    assert extract_usable_input_coverage(payload) is None


def test_pending_variant_cannot_claim_a_successful_publish(tmp_path, monkeypatch):
    from app.worker.runtime import TaskResult

    task, clock, bad, good, recent, calls, fail = _scheduled_task(tmp_path, monkeypatch)
    original = task._run

    async def paused(parameters):
        if parameters == bad:
            return TaskResult(status="paused", details={"result": "refreshed", "published": True})
        return await original(parameters)

    monkeypatch.setattr(task, "_run", paused)
    result = asyncio.run(task())
    assert result.status == "degraded"
    assert result.details["variant_refresh_published"] == 1
    assert result.details["variant_refresh_failed"] == 1
    assert result.details["variant_refresh_pending"] == 1
